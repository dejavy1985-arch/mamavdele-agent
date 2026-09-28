"""Диспетчер: единая точка обработки входящего сообщения.

Порядок строгий и безопасный:
  1. allowlist  - чужие сообщения игнорируются (тихо, но с записью в журнал);
  2. команды    - /projects, /status, /log, /help обрабатываются напрямую;
  3. границы    - запрос на запрещённое действие этапа 1 не выполняется;
  4. маршрут    - определяем проект; если неясно, просим уточнить; если проект
                  не подключён, честно сообщаем; иначе передаём задачу адаптеру;
  5. журнал     - каждое решение фиксируется.

Диспетчер не зависит от Telegram: он принимает user_id и text, а возвращает
структурированный ответ, который транспорт превращает в сообщение.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from . import audit as audit_mod
from . import router as router_mod
from .adapters import build_adapter
from .config import ControlCenterConfig
from .guardrails import check_boundaries
from .registry import Registry
from .security import Allowlist
from .state import State

# Типы ответа диспетчера.
DENIED = "denied"
BOUNDARY_BLOCKED = "boundary_blocked"
ROUTED = "routed"
CLARIFY = "clarify"
NOT_CONNECTED = "not_connected"
UNKNOWN = "unknown"
COMMAND = "command"


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

    # -- публичный вход -----------------------------------------------------
    def handle(self, user_id, text: str) -> Response:
        text = (text or "").strip()

        # 1. allowlist
        if not self.allowlist.is_allowed(user_id):
            self.audit.log(
                "message_denied", level=audit_mod.WARNING, user_id=user_id, text=text
            )
            return Response(
                type=DENIED,
                message="Доступ запрещён.",
                silent=True,
            )

        # 2. команды
        if text.startswith("/"):
            return self._handle_command(user_id, text)

        # 3. границы этапа 1
        boundary = check_boundaries(text)
        if boundary.blocked:
            self.audit.log(
                "boundary_blocked",
                level=audit_mod.WARNING,
                user_id=user_id,
                text=text,
                categories=boundary.categories,
            )
            return Response(
                type=BOUNDARY_BLOCKED,
                message=(
                    "Это действие вне рамок первого этапа "
                    f"({boundary.human()}). Я не выполняю его сам и не меняю "
                    "настройки агентов. Могу передать это вашему агенту как "
                    "черновик/поручение, если подтвердите."
                ),
                data={"categories": boundary.categories},
            )

        # 4. маршрутизация
        result = router_mod.route(text, self.registry.all())

        if result.outcome == router_mod.UNKNOWN:
            self.audit.log("route_unknown", user_id=user_id, text=text)
            return Response(
                type=UNKNOWN,
                message=(
                    "Не понял, к какому проекту это относится. "
                    "Список проектов: /projects"
                ),
            )

        if result.outcome == router_mod.AMBIGUOUS:
            names = [f"«{c.title}»" for c in result.candidates]
            self.audit.log(
                "route_ambiguous",
                user_id=user_id,
                text=text,
                candidates=[c.project_id for c in result.candidates],
            )
            return Response(
                type=CLARIFY,
                message=(
                    "Уточните проект. Подходят: " + ", ".join(names) + "."
                ),
                data={"candidates": [c.project_id for c in result.candidates]},
            )

        # MATCH
        best = result.best
        project = self.registry.get(best.project_id)
        if project is None:  # защита от рассинхрона
            return Response(type=UNKNOWN, message="Проект не найден в реестре.")

        if not project.is_connected():
            self.audit.log(
                "route_not_connected",
                level=audit_mod.WARNING,
                user_id=user_id,
                project=project.id,
                text=text,
            )
            return Response(
                type=NOT_CONNECTED,
                project_id=project.id,
                message=(
                    f"Проект «{project.title}» есть в списке, но сейчас не "
                    f"подключён (статус: {project.status}). Задачу не передаю."
                ),
            )

        # Проект подключён: формируем задачу и передаём адаптеру.
        task = {
            "id": uuid.uuid4().hex[:12],
            "user_id": user_id,
            "text": text,
            "project": project.id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.state.add_pending(task)

        adapter = build_adapter(
            project, cross_session_available=self.config.cross_session_available
        )
        delivery = adapter.deliver(project, task)

        self.audit.log(
            "task_routed",
            user_id=user_id,
            project=project.id,
            text=text,
            task_id=task["id"],
            delivered=delivery.delivered,
            adapter_kind=delivery.kind,
        )

        if delivery.delivered:
            # Локально записанное поручение считаем завершённым для центра:
            # дальше его ведёт агент проекта.
            self.state.resolve_pending(task["id"])

        return Response(
            type=ROUTED,
            project_id=project.id,
            message=f"Проект: «{project.title}».\n{delivery.message}",
            data={
                "task_id": task["id"],
                "adapter_kind": delivery.kind,
                "delivered": delivery.delivered,
            },
        )

    # -- команды ------------------------------------------------------------
    def _handle_command(self, user_id, text: str) -> Response:
        cmd = text.split()[0].lower()

        if cmd in ("/projects", "/list"):
            lines = ["Проекты:"]
            for p in self.registry.all():
                mark = "🟢" if p.is_connected() else "⚪️"
                lines.append(f"{mark} {p.title} ({p.id}) - {p.status}")
            if len(lines) == 1:
                lines.append("(пусто)")
            self.audit.log("cmd_projects", user_id=user_id)
            return Response(type=COMMAND, message="\n".join(lines))

        if cmd == "/status":
            connected = self.registry.connected()
            pending = self.state.pending_tasks()
            msg = (
                f"Проектов всего: {len(self.registry.all())}, "
                f"подключено: {len(connected)}.\n"
                f"Незавершённых задач: {len(pending)}.\n"
                f"Смещение Telegram: {self.state.telegram_offset}."
            )
            self.audit.log("cmd_status", user_id=user_id)
            return Response(type=COMMAND, message=msg)

        if cmd == "/log":
            events = self.audit.tail(10)
            if not events:
                return Response(type=COMMAND, message="Журнал пуст.")
            lines = ["Последние события:"]
            for e in events:
                lines.append(
                    f"{e.get('ts','')} [{e.get('level','')}] {e.get('event','')} "
                    f"{e.get('project','')}".rstrip()
                )
            return Response(type=COMMAND, message="\n".join(lines))

        if cmd in ("/help", "/start"):
            return Response(
                type=COMMAND,
                message=(
                    "Я центр управления вашими агентами.\n"
                    "Напишите обычным текстом задачу, я определю проект и передам её.\n"
                    "Команды: /projects, /status, /log, /help."
                ),
            )

        return Response(
            type=COMMAND,
            message="Неизвестная команда. /help покажет доступные.",
        )
