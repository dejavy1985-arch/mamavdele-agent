"""Хранилище на SQLite: очередь поручений, статусы, история, восстановление.

Идея взята из Jarvis (он держит проекты, чаты и сессии в локальной SQLite и
восстанавливается после перезапуска). Используется стандартный модуль sqlite3.

Что хранится:
  - projects         снимок проектов (id, заголовок, статус);
  - tasks            поручения: текст, статус, результат, куда вернуть ответ
                     (чат и сообщение Telegram), время начала и окончания;
  - events           журнал событий (история);
  - project_sessions сессия агента проекта для продолжения разговора (resume).

Потокобезопасность: исполнитель запускает агентов в фоновых потоках, поэтому все
операции идут под общей блокировкой. Долговечность: режим WAL, каждая запись
фиксируется сразу, перезапуск программы или сервера ничего не теряет.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from typing import Dict, Iterable, List, Optional

# Жизненный цикл поручения.
QUEUED = "queued"                                 # ждёт исполнителя
AWAITING_CONFIRMATION = "awaiting_confirmation"   # ждёт подтверждения опасного действия
PROCESSING = "processing"                         # агент выполняет
DONE = "done"                                     # агент выполнил
DELIVERED = "delivered"                           # только записано в папку, агент НЕ выполнял
FAILED = "failed"                                 # ошибка
CANCELLED = "cancelled"                           # отменено пользователем
REJECTED = "rejected"                             # отклонено при подтверждении
BLOCKED = "blocked"                               # заблокировано границами

ACTIVE_STATUSES = (QUEUED, AWAITING_CONFIRMATION, PROCESSING)
FINAL_STATUSES = (DONE, DELIVERED, FAILED, CANCELLED, REJECTED, BLOCKED)

# Колонки, добавленные позже: при открытии старой базы они дописываются.
_TASK_COLUMNS = {
    "chat_id": "INTEGER",
    "reply_to": "INTEGER",
    "started_at": "TEXT",
    "finished_at": "TEXT",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        # check_same_thread=False: доступ из фоновых потоков, но всегда под self._lock.
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self._create()

    def _create(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, title TEXT, status TEXT, updated_at TEXT
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
                    ts TEXT, level TEXT, event TEXT,
                    project_id TEXT, task_id TEXT, detail TEXT
                );
                CREATE TABLE IF NOT EXISTS project_sessions (
                    project_id TEXT PRIMARY KEY, session_handle TEXT, updated_at TEXT
                );
                """
            )
            existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(tasks)")}
            for col, ctype in _TASK_COLUMNS.items():
                if col not in existing:
                    self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ctype}")
            self.conn.commit()

    # -- проекты ------------------------------------------------------------
    def upsert_project(self, project_id: str, title: str, status: str) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO projects(id, title, status, updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                     status=excluded.status, updated_at=excluded.updated_at""",
                (project_id, title, status, _now()),
            )
            self.conn.commit()

    def projects(self) -> List[Dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM projects ORDER BY id")]

    # -- поручения ----------------------------------------------------------
    def enqueue_task(
        self,
        project_id: str,
        text: str,
        needs_confirmation: bool = False,
        chat_id: Optional[int] = None,
        reply_to: Optional[int] = None,
    ) -> Dict:
        task_id = _new_id()
        status = AWAITING_CONFIRMATION if needs_confirmation else QUEUED
        now = _now()
        with self._lock:
            self.conn.execute(
                """INSERT INTO tasks(id, project_id, text, status, needs_confirmation,
                                     created_at, updated_at, result, chat_id, reply_to)
                   VALUES(?,?,?,?,?,?,?,NULL,?,?)""",
                (task_id, project_id, text, status, 1 if needs_confirmation else 0,
                 now, now, chat_id, reply_to),
            )
            self.conn.commit()
            return self.get_task(task_id)

    def set_task_status(
        self, task_id: str, status: str, result: Optional[str] = None
    ) -> Optional[Dict]:
        with self._lock:
            finished = _now() if status in FINAL_STATUSES else None
            self.conn.execute(
                """UPDATE tasks SET status=?, result=COALESCE(?, result), updated_at=?,
                     finished_at=COALESCE(?, finished_at) WHERE id=?""",
                (status, result, _now(), finished, task_id),
            )
            self.conn.commit()
            return self.get_task(task_id)

    def claim_next(self, busy_projects: Iterable[str] = ()) -> Optional[Dict]:
        """Атомарно взять самую старую задачу в очереди из свободного проекта.

        Задачи одного проекта выполняются строго по очереди, разные проекты
        параллельно. Возвращает задачу уже в статусе processing или None."""
        busy = set(busy_projects)
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY created_at, rowid", (QUEUED,)
            ).fetchall()
            for row in rows:
                if row["project_id"] in busy:
                    continue
                now = _now()
                cur = self.conn.execute(
                    """UPDATE tasks SET status=?, started_at=?, updated_at=?
                       WHERE id=? AND status=?""",
                    (PROCESSING, now, now, row["id"], QUEUED),
                )
                self.conn.commit()
                if cur.rowcount == 1:
                    return self.get_task(row["id"])
                busy.add(row["project_id"])
            return None

    def count_ahead(self, task: Dict) -> int:
        """Сколько задач этого проекта стоит перед данной (в очереди и в работе)."""
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) AS n FROM tasks
                   WHERE project_id=? AND status IN (?, ?) AND id<>?
                     AND rowid < (SELECT rowid FROM tasks WHERE id=?)""",
                (task["project_id"], QUEUED, PROCESSING, task["id"], task["id"]),
            ).fetchone()
            return int(row["n"])

    def confirm_task(self, task_id: str) -> Optional[Dict]:
        with self._lock:
            task = self.get_task(task_id)
            if not task or task["status"] != AWAITING_CONFIRMATION:
                return None
            return self.set_task_status(task_id, QUEUED)

    def reject_task(self, task_id: str) -> Optional[Dict]:
        with self._lock:
            task = self.get_task(task_id)
            if not task or task["status"] not in (AWAITING_CONFIRMATION, QUEUED):
                return None
            return self.set_task_status(task_id, REJECTED)

    def next_queued(self, project_id: Optional[str] = None) -> Optional[Dict]:
        with self._lock:
            if project_id:
                row = self.conn.execute(
                    "SELECT * FROM tasks WHERE status=? AND project_id=? "
                    "ORDER BY created_at, rowid LIMIT 1",
                    (QUEUED, project_id),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY created_at, rowid LIMIT 1",
                    (QUEUED,),
                ).fetchone()
            return dict(row) if row else None

    def get_task(self, task_id: str) -> Optional[Dict]:
        with self._lock:
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
        with self._lock:
            rows = self.conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                params,
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
        with self._lock:
            self.conn.execute(
                """INSERT INTO events(ts, level, event, project_id, task_id, detail)
                   VALUES(?,?,?,?,?,?)""",
                (_now(), level, event, project_id, task_id, detail),
            )
            self.conn.commit()

    def history(self, limit: int = 20) -> List[Dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # -- сессии проектов (resume) ------------------------------------------
    def set_session(self, project_id: str, handle: str) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO project_sessions(project_id, session_handle, updated_at)
                   VALUES(?,?,?)
                   ON CONFLICT(project_id) DO UPDATE SET
                     session_handle=excluded.session_handle, updated_at=excluded.updated_at""",
                (project_id, handle, _now()),
            )
            self.conn.commit()

    def get_session(self, project_id: str) -> Optional[str]:
        with self._lock:
            row = self.conn.execute(
                "SELECT session_handle FROM project_sessions WHERE project_id=?",
                (project_id,),
            ).fetchone()
            return row["session_handle"] if row else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()
