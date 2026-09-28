"""Исполнитель: фоновые задачи агентов с возвратом результата в тот же чат.

Берёт поручения из очереди (SQLite), запускает агента проекта через адаптер,
сохраняет статус и результат, отправляет сообщение в чат, откуда пришла задача,
ответом на исходное сообщение.

Порядок: задачи одного проекта строго по очереди, разные проекты параллельно
(не больше max_parallel одновременно). Отмена: /cancel <id> останавливает агента.
Перезапуск центра: очередь сохраняется в базе и продолжает выполняться.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from . import audit as audit_mod
from . import store_db
from .adapters import build_adapter
from .paths import ProjectSandbox
from .runner import ExecContext, ExecResult

Notifier = Callable[[Optional[int], str, Optional[int]], None]

CHUNK = 3500           # символов в одном сообщении с результатом (предел Telegram 4096)
MAX_CHUNKS = 3         # сколько частей результата присылать сразу
START_NOTICE_SEC = 3   # сообщать «начал», если задача ждала в очереди дольше


def split_text(text: str, size: int = CHUNK) -> List[str]:
    """Разбить длинный текст на части, стараясь резать по строкам."""
    parts: List[str] = []
    rest = text
    while len(rest) > size:
        cut = rest.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest:
        parts.append(rest)
    return parts or [""]


def _seconds_between(a: Optional[str], b: Optional[str]) -> float:
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        return time.mktime(time.strptime(b, fmt)) - time.mktime(time.strptime(a, fmt))
    except (TypeError, ValueError):
        return 0.0


class Worker:
    def __init__(self, dispatcher, notifier: Optional[Notifier] = None,
                 max_parallel: int = 2, poll_interval: float = 0.5) -> None:
        self.d = dispatcher
        self.notifier = notifier or (lambda chat_id, text, reply_to=None: None)
        self.max_parallel = max(1, int(max_parallel))
        self.poll_interval = poll_interval
        self._running: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=self.max_parallel,
                                        thread_name_prefix="acc-agent")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        dispatcher.worker = self

    # -- жизненный цикл -----------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="acc-worker", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as exc:  # исполнитель не должен падать целиком
                self.d.audit.log("worker_error", level=audit_mod.ERROR, text=str(exc))
            self._stop.wait(self.poll_interval)

    def stop(self, reason: str = "Центр управления остановлен, задача прервана.") -> None:
        self._stop.set()
        with self._lock:
            for info in self._running.values():
                info["ctx"].reason = reason
                info["ctx"].cancel_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._pool.shutdown(wait=True)

    def running(self) -> Dict[str, Dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._running.items()}

    # -- выбор задач ----------------------------------------------------------
    def step(self) -> int:
        """Запустить столько задач из очереди, сколько позволяют свободные места."""
        started = 0
        while not self._stop.is_set():
            with self._lock:
                if len(self._running) >= self.max_parallel:
                    break
                busy = {v["project"] for v in self._running.values()}
            task = self.d.store.claim_next(busy)
            if task is None:
                break
            ctx = ExecContext(session_id=self.d.store.get_session(task["project_id"]))
            with self._lock:
                self._running[task["id"]] = {"project": task["project_id"], "ctx": ctx,
                                             "started": time.monotonic()}
            self._pool.submit(self._execute, task, ctx)
            started += 1
        return started

    def run_until_idle(self, timeout: float = 120.0) -> bool:
        """Выполнить очередь синхронно (для командной строки и тестов)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.step()
            with self._lock:
                busy = bool(self._running)
            if not busy and self.d.store.next_queued() is None:
                return True
            time.sleep(0.05)
        return False

    def cancel(self, task_id: str) -> Optional[str]:
        """Отменить задачу: остановить агента или убрать из очереди."""
        with self._lock:
            info = self._running.get(task_id)
            if info:
                info["ctx"].reason = "Задача отменена по вашей команде, агент остановлен."
                info["ctx"].cancel_event.set()
                return "running"
        task = self.d.store.get_task(task_id)
        if task and task["status"] in (store_db.QUEUED, store_db.AWAITING_CONFIRMATION):
            self.d.store.set_task_status(task_id, store_db.CANCELLED, result="отменено до запуска")
            self.d.store.record_event("task_cancelled", project_id=task["project_id"],
                                      task_id=task_id)
            return "queued"
        return None

    # -- выполнение -----------------------------------------------------------
    def _notify(self, task: Dict, text: str) -> None:
        try:
            self.notifier(task.get("chat_id"), text, task.get("reply_to"))
        except Exception as exc:  # сбой отправки не должен ломать учёт задачи
            self.d.audit.log("notify_error", level=audit_mod.ERROR,
                             task_id=task.get("id"), text=str(exc))

    def _execute(self, task: Dict, ctx: ExecContext) -> None:
        project = self.d.registry.get(task["project_id"])
        title = project.title if project else task["project_id"]
        try:
            if project is None or not project.is_connected():
                res = ExecResult(ok=False, executed=False,
                                 error="Проект сейчас не подключён, задача не выполнена.")
            else:
                if _seconds_between(task.get("created_at"), task.get("started_at")) >= START_NOTICE_SEC:
                    self._notify(task, f"▶️ «{title}» начал задачу #{task['id']}.")
                adapter = build_adapter(
                    project, cross_session_available=self.d.config.cross_session_available)
                res = adapter.execute(project, task, ctx)
        except Exception as exc:
            res = ExecResult(ok=False, executed=False, error=f"Внутренняя ошибка исполнителя: {exc}")
        try:
            self._finish(task, project, title, res)
        finally:
            with self._lock:
                self._running.pop(task["id"], None)

    def _finish(self, task: Dict, project, title: str, res: ExecResult) -> None:
        if res.cancelled:
            status = store_db.CANCELLED
        elif res.ok and not res.executed:
            status = store_db.DELIVERED
        elif res.ok:
            status = store_db.DONE
        else:
            status = store_db.FAILED

        if res.session_id and project is not None:
            self.d.store.set_session(project.id, res.session_id)

        body = res.output if res.ok else (res.error or res.output)
        self.d.store.set_task_status(task["id"], status, result=body)
        self.d.store.record_event(f"task_{status}", level="info" if res.ok else "warning",
                                  project_id=task["project_id"], task_id=task["id"],
                                  detail=f"{res.duration:.1f}s isolated={res.isolated}")
        self.d.audit.log(f"task_{status}", project=task["project_id"], task_id=task["id"],
                         duration=round(res.duration, 2), exit_code=res.exit_code,
                         isolated=res.isolated, executed=res.executed)
        if project is not None and status in (store_db.DONE, store_db.FAILED):
            self._save_result(project, task, status, res)

        secs = f"за {res.duration:.0f} с" if res.duration >= 1 else "менее чем за секунду"
        tail = ""
        if res.notes:
            tail = "\n\nℹ️ " + "; ".join(res.notes)
        if status == store_db.DONE:
            head = f"✅ «{title}» выполнил задачу #{task['id']} {secs}."
        elif status == store_db.DELIVERED:
            head = f"📁 «{title}»: задача #{task['id']} только записана в папку, агент её не выполнял."
        elif status == store_db.CANCELLED:
            head = f"⏹ Задача #{task['id']} («{title}») остановлена."
        else:
            head = f"❌ «{title}»: задача #{task['id']} не выполнена."

        parts = split_text(body or "(агент ничего не ответил)")
        first = f"{head}\n\n{parts[0]}"
        if len(parts) == 1:
            self._notify(task, first + tail)
            return
        self._notify(task, first)
        for p in parts[1:MAX_CHUNKS]:
            self._notify(task, p)
        if len(parts) > MAX_CHUNKS:
            self._notify(task, f"Показана часть ответа. Полностью: /result {task['id']}{tail}")
        elif tail:
            self._notify(task, tail.strip())

    def _save_result(self, project, task: Dict, status: str, res: ExecResult) -> None:
        """Сохранить результат в историю самого домика (results/<id>.md)."""
        try:
            sb = ProjectSandbox(project.home_dir)
            text = (
                f"# Задача {task['id']}\n\nСтатус: {status}\nСоздана: {task.get('created_at')}\n"
                f"Длительность: {res.duration:.1f} с\nВ песочнице: {'да' if res.isolated else 'нет'}\n\n"
                f"## Текст задачи\n\n{task['text']}\n\n## Ответ агента\n\n"
                f"{res.output if res.ok else (res.error or res.output)}\n"
            )
            sb.write_text(os.path.join("results", f"{task['id']}.md"), text)
        except Exception as exc:
            self.d.audit.log("result_save_error", level=audit_mod.WARNING,
                             task_id=task["id"], text=str(exc))
