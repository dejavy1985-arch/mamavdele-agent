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

import json
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

from . import audit as audit_mod
from . import router as router_mod
from . import store_db
from .adapters import HANDOFF_READY, build_adapter
from .config import ControlCenterConfig
from .guardrails import check_boundaries, check_risky
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
NEEDS_CONFIRMATION = "needs_confirmation"
ERROR = "error"


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
        # SQLite: очередь поручений, статусы, история, дескрипторы сессий.
        self.store = store_db.Store(os.path.join(config.var_dir, "acc.db"))
        for p in self.registry.all():
            self.store.upsert_project(p.id, p.title, p.status)

    # -- восстановление после перезапуска -----------------------------------
    def recover_after_restart(self) -> Dict[str, int]:
        """Подхватить поручения, прерванные падением или перезапуском.

        - queued: поручение принято, но не успело уйти агенту. Передаём сейчас.
        - processing без результата: передача оборвалась на середине, неизвестно,
          дошло ли поручение. Не дублируем, а честно помечаем failed.
        - awaiting_confirmation: ничего не делаем, их можно подтвердить и после
          перезапуска (/confirm).
        Вызывается явно при старте бота и командой `python -m acc.cli recover`.
        """
        counts = {"redelivered": 0, "interrupted": 0, "unavailable": 0}

        for t in self.store.list_tasks(status=store_db.PROCESSING, limit=1000):
            if t.get("result") is None:
                self.store.set_task_status(t["id"], store_db.FAILED,
                                           result="interrupted_by_restart")
                self.store.record_event("task_interrupted", level="warning",
                                        project_id=t["project_id"], task_id=t["id"])
                counts["interrupted"] += 1

        queued = self.store.list_tasks(status=store_db.QUEUED, limit=1000)
        for t in sorted(queued, key=lambda x: x["created_at"] or ""):
            project = self.registry.get(t["project_id"])
            if project is None or not project.is_connected():
                self.store.set_task_status(t["id"], store_db.FAILED,
                                           result="project_unavailable")
                counts["unavailable"] += 1
                continue
            self._deliver(None, project, t)
            counts["redelivered"] += 1

        self.store.record_event("recovered_after_restart",
                                detail=json.dumps(counts, ensure_ascii=False))
        self.audit.log("recovered_after_restart", **counts)
        return counts

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

        # Проект подключён. Опасное, но разрешённое действие требует подтверждения
        # (аналог Plan Mode и подтверждения инструментов в Jarvis).
        risk = check_risky(text)
        if risk.risky:
            db_task = self.store.enqueue_task(project.id, text, needs_confirmation=True)
            self.store.record_event(
                "task_awaiting_confirmation", project_id=project.id,
                task_id=db_task["id"], detail=",".join(risk.categories),
            )
            self.audit.log(
                "task_awaiting_confirmation", level=audit_mod.WARNING,
                user_id=user_id, project=project.id, text=text,
                task_id=db_task["id"], categories=risk.categories,
            )
            return Response(
                type=NEEDS_CONFIRMATION,
                project_id=project.id,
                message=(
                    f"Проект: «{project.title}».\nЭто опасное действие "
                    f"({risk.human()}). Подтвердите: /confirm {db_task['id']} "
                    f"или отмените: /reject {db_task['id']}."
                ),
                data={"task_id": db_task["id"], "categories": risk.categories},
            )

        # Обычное поручение: ставим в очередь и передаём адаптеру.
        db_task = self.store.enqueue_task(project.id, text, needs_confirmation=False)
        return self._deliver(user_id, project, db_task)

    # -- доставка поручения агенту через адаптер ---------------------------
    def _deliver(self, user_id, project, db_task: Dict) -> Response:
        self.store.set_task_status(db_task["id"], store_db.PROCESSING)
        task = {
            "id": db_task["id"],
            "user_id": user_id,
            "text": db_task["text"],
            "project": project.id,
            "created_at": db_task.get("created_at"),
        }
        adapter = build_adapter(
            project, cross_session_available=self.config.cross_session_available
        )
        try:
            delivery = adapter.deliver(project, task)
        except Exception as exc:  # ошибка доставки не должна оставлять задачу зависшей
            self.store.set_task_status(db_task["id"], store_db.FAILED, result=f"error: {exc}")
            self.store.record_event("task_failed", level="error", project_id=project.id,
                                    task_id=db_task["id"], detail=str(exc)[:300])
            self.audit.log("task_failed", level=audit_mod.ERROR, user_id=user_id,
                           project=project.id, task_id=db_task["id"], error=str(exc)[:300])
            return Response(
                type=ERROR,
                project_id=project.id,
                message="Не удалось передать поручение. Ошибка записана в журнал.",
                data={"task_id": db_task["id"]},
            )

        # Итоговый статус: доставлено -> done; готово к передаче в живую сессию ->
        # остаётся processing (ждёт подтверждения доставки); иначе failed, чтобы
        # недоставленная задача не висела в очереди как "выполняется".
        if delivery.delivered:
            final_status = store_db.DONE
        elif delivery.kind == HANDOFF_READY:
            final_status = store_db.PROCESSING
        else:
            final_status = store_db.FAILED
        self.store.set_task_status(db_task["id"], final_status, result=delivery.kind)
        self.store.record_event(
            "task_routed", project_id=project.id, task_id=db_task["id"],
            detail=delivery.kind,
        )
        self.audit.log(
            "task_routed", user_id=user_id, project=project.id,
            task_id=db_task["id"], delivered=delivery.delivered,
            adapter_kind=delivery.kind,
        )
        return Response(
            type=ROUTED,
            project_id=project.id,
            message=f"Проект: «{project.title}».\n{delivery.message}",
            data={
                "task_id": db_task["id"],
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
            active = [
                t for t in self.store.list_tasks(limit=200)
                if t["status"] in store_db.ACTIVE_STATUSES
            ]
            msg = (
                f"Проектов всего: {len(self.registry.all())}, "
                f"подключено: {len(connected)}.\n"
                f"Активных задач: {len(active)}.\n"
                f"Смещение Telegram: {self.state.telegram_offset}."
            )
            self.audit.log("cmd_status", user_id=user_id)
            return Response(type=COMMAND, message=msg)

        if cmd == "/queue":
            active = [
                t for t in self.store.list_tasks(limit=200)
                if t["status"] in store_db.ACTIVE_STATUSES
            ]
            if not active:
                return Response(type=COMMAND, message="Очередь пуста.")
            lines = ["Очередь:"]
            for t in active[:20]:
                lines.append(f"{t['id']} [{t['status']}] {t['project_id']}: {t['text'][:60]}")
            return Response(type=COMMAND, message="\n".join(lines))

        if cmd == "/history":
            events = self.store.history(15)
            if not events:
                return Response(type=COMMAND, message="История пуста.")
            lines = ["История:"]
            for e in events:
                lines.append(
                    f"{e.get('ts','')} {e.get('event','')} "
                    f"{e.get('project_id') or ''}".rstrip()
                )
            return Response(type=COMMAND, message="\n".join(lines))

        if cmd in ("/confirm", "/reject"):
            parts = text.split()
            if len(parts) < 2:
                return Response(type=COMMAND, message=f"Укажите id задачи: {cmd} <id>.")
            task_id = parts[1]
            task = self.store.get_task(task_id)
            if not task:
                return Response(type=COMMAND, message="Задача не найдена.")
            if cmd == "/reject":
                if not self.store.reject_task(task_id):
                    return Response(
                        type=COMMAND,
                        message=f"Отклонить нельзя: задача уже в статусе {task['status']}.",
                    )
                self.store.record_event("task_rejected", project_id=task["project_id"],
                                        task_id=task_id)
                self.audit.log("task_rejected", user_id=user_id, project=task["project_id"],
                               task_id=task_id)
                return Response(type=COMMAND, message="Отклонено.")
            # /confirm
            confirmed = self.store.confirm_task(task_id)
            if not confirmed:
                return Response(type=COMMAND, message="Нечего подтверждать (задача не ждёт подтверждения).")
            project = self.registry.get(task["project_id"])
            if project is None or not project.is_connected():
                self.store.set_task_status(task_id, store_db.FAILED, result="project_unavailable")
                return Response(type=COMMAND, message="Проект недоступен, задача не передана.")
            self.audit.log("task_confirmed", user_id=user_id, project=project.id, task_id=task_id)
            return self._deliver(user_id, project, self.store.get_task(task_id))

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
                    "Опасные действия спрошу подтвердить.\n"
                    "Команды: /projects, /status, /queue, /history, /log, "
                    "/confirm <id>, /reject <id>, /help."
                ),
            )

        return Response(
            type=COMMAND,
            message="Неизвестная команда. /help покажет доступные.",
        )
