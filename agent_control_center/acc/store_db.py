"""Хранилище на SQLite: очередь поручений, статусы, история, восстановление.

Идея взята из Jarvis (он держит проекты, чаты и сессии в локальной SQLite и
восстанавливается после перезапуска). Здесь используется стандартный модуль
sqlite3, без сторонних зависимостей.

Что хранится:
  - projects        снимок проектов (id, заголовок, статус);
  - tasks           поручения с жизненным циклом статусов и результатом;
  - events          журнал событий (история) для запросов из Telegram;
  - project_sessions дескриптор сессии проекта для возобновления (resume).

Долговечность: включён режим WAL и обычные транзакции sqlite. Перезапуск
программы или компьютера ничего не теряет: при повторном открытии того же файла
все поручения, статусы и история на месте.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from typing import Dict, List, Optional

# Жизненный цикл поручения.
QUEUED = "queued"                       # готово к передаче агенту
AWAITING_CONFIRMATION = "awaiting_confirmation"  # ждёт подтверждения опасного действия
PROCESSING = "processing"               # передано агенту, выполняется
DONE = "done"                           # завершено
FAILED = "failed"                       # ошибка
REJECTED = "rejected"                   # отклонено пользователем
BLOCKED = "blocked"                     # заблокировано границами этапа 1

ACTIVE_STATUSES = (QUEUED, AWAITING_CONFIRMATION, PROCESSING)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self._create()

    def _create(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                text TEXT,
                status TEXT,
                needs_confirmation INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                result TEXT
            );
            CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                level TEXT,
                event TEXT,
                project_id TEXT,
                task_id TEXT,
                detail TEXT
            );
            CREATE TABLE IF NOT EXISTS project_sessions (
                project_id TEXT PRIMARY KEY,
                session_handle TEXT,
                updated_at TEXT
            );
            """
        )
        self.conn.commit()

    # -- проекты ------------------------------------------------------------
    def upsert_project(self, project_id: str, title: str, status: str) -> None:
        self.conn.execute(
            """INSERT INTO projects(id, title, status, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                 status=excluded.status, updated_at=excluded.updated_at""",
            (project_id, title, status, _now()),
        )
        self.conn.commit()

    def projects(self) -> List[Dict]:
        rows = self.conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # -- поручения ----------------------------------------------------------
    def enqueue_task(
        self, project_id: str, text: str, needs_confirmation: bool = False
    ) -> Dict:
        task_id = _new_id()
        status = AWAITING_CONFIRMATION if needs_confirmation else QUEUED
        now = _now()
        self.conn.execute(
            """INSERT INTO tasks(id, project_id, text, status, needs_confirmation,
                                 created_at, updated_at, result)
               VALUES(?,?,?,?,?,?,?,NULL)""",
            (task_id, project_id, text, status, 1 if needs_confirmation else 0, now, now),
        )
        self.conn.commit()
        return self.get_task(task_id)

    def set_task_status(
        self, task_id: str, status: str, result: Optional[str] = None
    ) -> Optional[Dict]:
        self.conn.execute(
            "UPDATE tasks SET status=?, result=COALESCE(?, result), updated_at=? WHERE id=?",
            (status, result, _now(), task_id),
        )
        self.conn.commit()
        return self.get_task(task_id)

    def confirm_task(self, task_id: str) -> Optional[Dict]:
        task = self.get_task(task_id)
        if not task or task["status"] != AWAITING_CONFIRMATION:
            return None
        return self.set_task_status(task_id, QUEUED)

    def reject_task(self, task_id: str) -> Optional[Dict]:
        task = self.get_task(task_id)
        if not task or task["status"] not in (AWAITING_CONFIRMATION, QUEUED):
            return None
        return self.set_task_status(task_id, REJECTED)

    def next_queued(self, project_id: Optional[str] = None) -> Optional[Dict]:
        if project_id:
            row = self.conn.execute(
                "SELECT * FROM tasks WHERE status=? AND project_id=? ORDER BY created_at LIMIT 1",
                (QUEUED, project_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY created_at LIMIT 1",
                (QUEUED,),
            ).fetchone()
        return dict(row) if row else None

    def get_task(self, task_id: str) -> Optional[Dict]:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(
        self, status: Optional[str] = None, project_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict]:
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?", params
        ).fetchall()
        return [dict(r) for r in rows]

    # -- история ------------------------------------------------------------
    def record_event(
        self,
        event: str,
        level: str = "info",
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO events(ts, level, event, project_id, task_id, detail)
               VALUES(?,?,?,?,?,?)""",
            (_now(), level, event, project_id, task_id, detail),
        )
        self.conn.commit()

    def history(self, limit: int = 20) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- сессии проектов (resume) ------------------------------------------
    def set_session(self, project_id: str, handle: str) -> None:
        self.conn.execute(
            """INSERT INTO project_sessions(project_id, session_handle, updated_at)
               VALUES(?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET session_handle=excluded.session_handle,
                 updated_at=excluded.updated_at""",
            (project_id, handle, _now()),
        )
        self.conn.commit()

    def get_session(self, project_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT session_handle FROM project_sessions WHERE project_id=?",
            (project_id,),
        ).fetchone()
        return row["session_handle"] if row else None

    def close(self) -> None:
        self.conn.close()
