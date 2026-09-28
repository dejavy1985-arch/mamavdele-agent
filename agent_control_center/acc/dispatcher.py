"""Диспетчер: единая точка обработки входящего сообщения.

Порядок строгий и безопасный:
  1. allowlist  - чужие сообщения игнорируются (тихо, но с записью в журнал);
  2. команды    - /projects, /status, /queue, /cancel и т.д. обрабатываются сразу;
  3. границы    - запрос на запрещённое действие этапа 1 не выполняется;
  4. маршрут    - определяем проект; если неясно, просим уточнить; если проект
                  не подключён, честно сообщаем;
  5. очередь    - задача ставится в очередь с адресом чата для ответа; опасная
                  задача сначала ждёт /confirm; выполняет её исполнитель (worker.py),
                  он же присылает статус и результат в тот же чат;
  6. журнал     - каждое решение фиксируется.

Диспетчер не зависит от Telegram: он принимает user_id и text, а возвращает
структурированный ответ, который транспорт превращает в сообщение.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import audit as audit_mod
from . import router as router_mod
from . import store_db
from .config import ControlCenterConfig
from .guardrails import check_boundaries, check_risky
from .registry import Registry
from .security import Allowlist
from .state import State

# Типы ответа диспетчера.
DENIED = "denied"
BOUNDARY_BLOCKED = "boundary_blocked"
ACCEPTED = "accepted"            # задача принята в очередь на выполнение
CLARIFY = "clarify"
NOT_CONNECTED = "not_connected"
UNKNOWN = "unknown"
COMMAND = "command"
NEEDS_CONFIRMATION = "needs_confirmation"
ERROR = "error"

STATUS_RU = {
    store_db.QUEUED: "в очереди",
    store_db.AWAITING_CONFIRMATION: "ждёт подтверждения",
    store_db.PROCESSING: "выполняется",
    store_db.DONE: "выполнена",
    store_db.DELIVERED: "только записана в папку",
    store_db.FAILED: "не выполнена",
    store_db.CANCELLED: "отменена",
    store_db.REJECTED: "отклонена",
    store_db.BLOCKED: "заблокирована",
}

RESULT_PAGE = 3500


@dataclass
class Response:
    type: str
    message: str
    project_id: Optional[str] = None
    data: Dict = field(default_factory=dict)
    silent: bool = False  # True -> транспорт не отвечает отправителю


class Dispatcher:
    def __init__(self, config: ControlCenterConfig) -> None:
        self.config = config
        self.registry = Registry(config.homes_dir).load()
        self.allowlist = Allowlist(config.allowed_user_ids)
        self.audit = audit_mod.AuditLog(config.audit_path)
        self.state = State(config.state_path)
        # SQLite: очередь поручений, статусы, история, сессии агентов.
        self.store = store_db.Store(os.path.join(config.var_dir, "acc.db"))
        for p in self.registry.all():
            self.store.upsert_project(p.id, p.title, p.status)
        self.worker = None               # подключается исполнителем (worker.Worker)
        self.interrupted_tasks: List[Dict] = []

    # -- восстановление после перезапуска -----------------------------------
    def recover_after_restart(self) -> Dict[str, int]:
        """Разобрать задачи, оставшиеся после падения или перезапуска.

        - processing: агент был остановлен вместе с центром. Результат неизвестен,
          поэтому задача честно помечается failed (её можно отправить заново);
          список сохраняется в self.interrupted_tasks, чтобы сообщить в их чаты;
        - queued: остаются в очереди, исполнитель выполнит их после старта;
        - awaiting_confirmation: остаются, подтвердить можно и после перезапуска.
        """
        self.interrupted_tasks = []
        for t in self.store.list_tasks(status=store_db.PROCESSING, limit=1000):
            self.store.set_task_status(
                t["id"], store_db.FAILED,
                result="Выполнение прервано перезапуском центра. Отправьте задачу заново.")
            self.store.record_event("task_interrupted", level="warning",
                                    project_id=t["project_id"], task_id=t["id"])
            self.interrupted_tasks.append(self.store.get_task(t["id"]))
        counts = {
            "interrupted": len(self.interrupted_tasks),
            "resumed": len(self.store.list_tasks(status=store_db.QUEUED, limit=1000)),
            "awaiting": len(self.store.list_tasks(status=store_db.AWAITING_CONFIRMATION,
                                                  limit=1000)),
        }
        self.store.record_event("recovered_after_restart",
                                detail=json.dumps(counts, ensure_ascii=False))
        self.audit.log("recovered_after_restart", **counts)
        return counts

    # -- публичный вход -----------------------------------------------------
    def handle(
        self,
        user_id,
        text: str,
        *,
        trusted: bool = False,
        project_id: Optional[str] = None,
        chat_id: Optional[int] = None,
        reply_to: Optional[int] = None,
    ) -> Response:
        """Обработать сообщение.

        trusted:    запрос из доверенного канала (командная строка, приложение Клопа),
                    доступ уже проверен там; границы и подтверждения действуют так же.
        project_id: проект выбран вручную, автоматическое определение пропускается.
        chat_id, reply_to: куда вернуть статус и результат (чат и сообщение Telegram).
        """
        text = (text or "").strip()

        # 1. allowlist
        if not trusted and not self.allowlist.is_allowed(user_id):
            self.audit.log("message_denied", level=audit_mod.WARNING, user_id=user_id, text=text)
            return Response(type=DENIED, message="Доступ запрещён.", silent=True)

        # 2. команды (/to <проект> <текст> выбирает проект вручную)
        if text.startswith("/"):
            parts = text.split(maxsplit=2)
            if parts[0].lower() == "/to":
                if len(parts) < 3:
                    return Response(type=COMMAND, message="Формат: /to <id проекта> <задача>.")
                project_id, text = parts[1], parts[2]
            else:
                return self._handle_command(user_id, text)

        if not text:
            return Response(type=UNKNOWN, message="Пустое сообщение. /help покажет команды.")

        # 3. границы этапа 1
        boundary = check_boundaries(text)
        if boundary.blocked:
            self.audit.log("boundary_blocked", level=audit_mod.WARNING, user_id=user_id,
                           text=text, categories=boundary.categories)
            return Response(
                type=BOUNDARY_BLOCKED,
                message=(
                    "Это действие вне рамок первого этапа "
                    f"({boundary.human()}). Я не выполняю его и не передаю агентам."
                ),
                data={"categories": boundary.categories},
            )

        # 4. маршрутизация (или проект, выбранный вручную)
        if project_id is not None:
            chosen = self.registry.get(project_id)
            if chosen is None:
                return Response(type=UNKNOWN, message="Такого проекта нет в списке. /projects")
            return self._to_project(user_id, chosen, text, chat_id, reply_to)

        result = router_mod.route(text, self.registry.all())
        if result.outcome == router_mod.UNKNOWN:
            self.audit.log("route_unknown", user_id=user_id, text=text)
            return Response(
                type=UNKNOWN,
                message=("Не понял, к какому проекту это относится. Список: /projects\n"
                         "Можно указать явно: /to <id проекта> <задача>."),
            )
        if result.outcome == router_mod.AMBIGUOUS:
            names = [f"«{c.title}» ({c.project_id})" for c in result.candidates]
            self.audit.log("route_ambiguous", user_id=user_id, text=text,
                           candidates=[c.project_id for c in result.candidates])
            return Response(
                type=CLARIFY,
                message=("Уточните проект. Подходят: " + ", ".join(names) + ".\n"
                         "Напишите: /to <id проекта> <задача>."),
                data={"candidates": [c.project_id for c in result.candidates]},
            )

        project = self.registry.get(result.best.project_id)
        if project is None:  # защита от рассинхрона
            return Response(type=UNKNOWN, message="Проект не найден в реестре.")
        return self._to_project(user_id, project, text, chat_id, reply_to)

    def _to_project(self, user_id, project, text: str,
                    chat_id: Optional[int] = None, reply_to: Optional[int] = None) -> Response:
        """Поставить задачу конкретному проекту: подключение, риск, очередь."""
        if not project.is_connected():
            self.audit.log("route_not_connected", level=audit_mod.WARNING, user_id=user_id,
                           project=project.id, text=text)
            return Response(
                type=NOT_CONNECTED,
                project_id=project.id,
                message=(f"Проект «{project.title}» есть в списке, но сейчас не подключён "
                         f"(статус: {project.status}). Задачу не передаю."),
            )

        # Опасное, но разрешённое действие ждёт подтверждения (аналог Plan Mode Jarvis).
        risk = check_risky(text)
        if risk.risky:
            task = self.store.enqueue_task(project.id, text, needs_confirmation=True,
                                           chat_id=chat_id, reply_to=reply_to)
            self.store.record_event("task_awaiting_confirmation", project_id=project.id,
                                    task_id=task["id"], detail=",".join(risk.categories))
            self.audit.log("task_awaiting_confirmation", level=audit_mod.WARNING,
                           user_id=user_id, project=project.id, text=text,
                           task_id=task["id"], categories=risk.categories)
            return Response(
                type=NEEDS_CONFIRMATION,
                project_id=project.id,
                message=(f"Проект: «{project.title}».\nЭто опасное действие "
                         f"({risk.human()}). Подтвердите: /confirm {task['id']} "
                         f"или отмените: /reject {task['id']}."),
                data={"task_id": task["id"], "categories": risk.categories},
            )

        task = self.store.enqueue_task(project.id, text, chat_id=chat_id, reply_to=reply_to)
        self.store.record_event("task_accepted", project_id=project.id, task_id=task["id"])
        self.audit.log("task_accepted", user_id=user_id, project=project.id, text=text,
                       task_id=task["id"])
        return self._accepted(project, task, prefix="📥 Принято.")

    def _accepted(self, project, task: Dict, prefix: str) -> Response:
        ahead = self.store.count_ahead(task)
        lines = [f"{prefix} Проект: «{project.title}». Задача #{task['id']}."]
        lines.append("Запускаю." if ahead == 0 else f"Перед ней в очереди: {ahead}.")
        if (project.adapter or {}).get("type", "manual") == "manual":
            lines.append("Внимание: этот проект не запускает агента автоматически, "
                         "поручение будет только записано в его папку.")
        lines.append(f"Результат пришлю сюда. Отменить: /cancel {task['id']}")
        return Response(type=ACCEPTED, project_id=project.id, message="\n".join(lines),
                        data={"task_id": task["id"], "ahead": ahead})

    # -- команды ------------------------------------------------------------
    def _handle_command(self, user_id, text: str) -> Response:
        parts = text.split()
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if cmd in ("/projects", "/list"):
            lines = ["Проекты:"]
            for p in self.registry.all():
                mark = "🟢" if p.is_connected() else "⚪️"
                test = " 🧪 тестовый" if p.test else ""
                kind = (p.adapter or {}).get("type", "manual")
                lines.append(f"{mark} {p.title} ({p.id}): {p.status}, агент: {kind}{test}")
            if len(lines) == 1:
                lines.append("(пусто)")
            self.audit.log("cmd_projects", user_id=user_id)
            return Response(type=COMMAND, message="\n".join(lines))

        if cmd == "/status":
            if arg:
                return self._task_status(arg)
            connected = self.registry.connected()
            queued = self.store.list_tasks(status=store_db.QUEUED, limit=500)
            awaiting = self.store.list_tasks(status=store_db.AWAITING_CONFIRMATION, limit=500)
            lines = [
                f"Проектов: {len(self.registry.all())}, подключено: {len(connected)}.",
                f"В очереди: {len(queued)}, ждут подтверждения: {len(awaiting)}.",
            ]
            running = self.worker.running() if self.worker else {}
            if running:
                lines.append("Выполняются сейчас:")
                for tid, info in running.items():
                    secs = int(time.monotonic() - info["started"])
                    lines.append(f"  #{tid} ({info['project']}), {secs} с")
            else:
                lines.append("Сейчас ничего не выполняется.")
            if self.worker is None:
                lines.append("Исполнитель не запущен: задачи ждут запуска центра.")
            self.audit.log("cmd_status", user_id=user_id)
            return Response(type=COMMAND, message="\n".join(lines))

        if cmd == "/queue":
            active = [t for t in self.store.list_tasks(limit=200)
                      if t["status"] in store_db.ACTIVE_STATUSES]
            if not active:
                return Response(type=COMMAND, message="Очередь пуста.")
            lines = ["Очередь:"]
            for t in reversed(active[:20]):
                lines.append(f"#{t['id']} [{STATUS_RU.get(t['status'], t['status'])}] "
                             f"{t['project_id']}: {t['text'][:60]}")
            return Response(type=COMMAND, message="\n".join(lines))

        if cmd == "/history":
            events = self.store.history(15)
            if not events:
                return Response(type=COMMAND, message="История пуста.")
            lines = ["История:"]
            for e in events:
                tid = f" #{e['task_id']}" if e.get("task_id") else ""
                lines.append(f"{e.get('ts','')} {e.get('event','')}{tid} "
                             f"{e.get('project_id') or ''}".rstrip())
            return Response(type=COMMAND, message="\n".join(lines))

        if cmd == "/result":
            if not arg:
                return Response(type=COMMAND, message="Формат: /result <id> [страница].")
            task = self.store.get_task(arg.lstrip("#"))
            if not task:
                return Response(type=COMMAND, message="Задача не найдена.")
            body = task.get("result") or "(результата пока нет)"
            pages = [body[i:i + RESULT_PAGE] for i in range(0, len(body), RESULT_PAGE)] or [""]
            try:
                n = max(1, min(int(parts[2]), len(pages))) if len(parts) > 2 else 1
            except ValueError:
                n = 1
            head = (f"Задача #{task['id']} ({STATUS_RU.get(task['status'], task['status'])}), "
                    f"страница {n} из {len(pages)}:")
            more = f"\n\nДальше: /result {task['id']} {n + 1}" if n < len(pages) else ""
            return Response(type=COMMAND, message=f"{head}\n\n{pages[n - 1]}{more}")

        if cmd == "/cancel":
            if not arg:
                return Response(type=COMMAND, message="Формат: /cancel <id>.")
            task_id = arg.lstrip("#")
            task = self.store.get_task(task_id)
            if not task:
                return Response(type=COMMAND, message="Задача не найдена.")
            outcome = None
            if self.worker is not None:
                outcome = self.worker.cancel(task_id)
            elif task["status"] in (store_db.QUEUED, store_db.AWAITING_CONFIRMATION):
                self.store.set_task_status(task_id, store_db.CANCELLED, result="отменено до запуска")
                outcome = "queued"
            self.audit.log("cmd_cancel", user_id=user_id, task_id=task_id, outcome=outcome)
            if outcome == "running":
                return Response(type=COMMAND, message=f"Останавливаю агента, задача #{task_id}.")
            if outcome == "queued":
                return Response(type=COMMAND, message=f"Задача #{task_id} отменена, агент её не запускал.")
            return Response(type=COMMAND, message=(
                f"Отменить нельзя: задача #{task_id} уже "
                f"{STATUS_RU.get(task['status'], task['status'])}."))

        if cmd in ("/confirm", "/reject"):
            if not arg:
                return Response(type=COMMAND, message=f"Укажите id задачи: {cmd} <id>.")
            task_id = arg.lstrip("#")
            task = self.store.get_task(task_id)
            if not task:
                return Response(type=COMMAND, message="Задача не найдена.")
            if cmd == "/reject":
                if not self.store.reject_task(task_id):
                    return Response(type=COMMAND, message=(
                        f"Отклонить нельзя: задача уже "
                        f"{STATUS_RU.get(task['status'], task['status'])}."))
                self.store.record_event("task_rejected", project_id=task["project_id"],
                                        task_id=task_id)
                self.audit.log("task_rejected", user_id=user_id, project=task["project_id"],
                               task_id=task_id)
                return Response(type=COMMAND, message="Отклонено, агент задачу не получит.")
            project = self.registry.get(task["project_id"])
            if project is None or not project.is_connected():
                return Response(type=COMMAND, message="Проект недоступен, задача не передана.")
            if not self.store.confirm_task(task_id):
                return Response(type=COMMAND,
                                message="Нечего подтверждать (задача не ждёт подтверждения).")
            self.store.record_event("task_confirmed", project_id=project.id, task_id=task_id)
            self.audit.log("task_confirmed", user_id=user_id, project=project.id, task_id=task_id)
            return self._accepted(project, self.store.get_task(task_id), prefix="✔️ Подтверждено.")

        if cmd == "/log":
            events = self.audit.tail(10)
            if not events:
                return Response(type=COMMAND, message="Журнал пуст.")
            lines = ["Последние события:"]
            for e in events:
                lines.append(f"{e.get('ts','')} [{e.get('level','')}] {e.get('event','')} "
                             f"{e.get('project','')}".rstrip())
            return Response(type=COMMAND, message="\n".join(lines))

        if cmd in ("/help", "/start"):
            return Response(
                type=COMMAND,
                message=(
                    "Я центр управления вашими агентами.\n"
                    "Напишите задачу обычным текстом: я определю проект, агент выполнит её, "
                    "статус и результат придут сюда. Опасные действия спрошу подтвердить.\n\n"
                    "/projects проекты и их агенты\n"
                    "/status общее состояние, /status <id> одна задача\n"
                    "/queue очередь, /history история\n"
                    "/result <id> полный ответ агента\n"
                    "/cancel <id> остановить задачу\n"
                    "/confirm <id>, /reject <id> решение по опасной задаче\n"
                    "/to <id проекта> <задача> выбрать проект вручную\n"
                    "/log журнал"
                ),
            )

        return Response(type=COMMAND, message="Неизвестная команда. /help покажет доступные.")

    def _task_status(self, task_id: str) -> Response:
        task = self.store.get_task(task_id.lstrip("#"))
        if not task:
            return Response(type=COMMAND, message="Задача не найдена.")
        lines = [
            f"Задача #{task['id']}: {STATUS_RU.get(task['status'], task['status'])}.",
            f"Проект: {task['project_id']}.",
            f"Создана: {task.get('created_at')}.",
        ]
        if task.get("started_at"):
            lines.append(f"Начата: {task['started_at']}.")
        if task.get("finished_at"):
            lines.append(f"Завершена: {task['finished_at']}.")
        if task.get("result"):
            lines.append(f"Ответ целиком: /result {task['id']}")
        return Response(type=COMMAND, message="\n".join(lines))
