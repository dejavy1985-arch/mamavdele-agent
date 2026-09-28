"""Адаптеры доставки задачи агенту проекта.

Важно для честности: адаптеры НЕ выполняют внешних действий (не публикуют, не
пишут клиентам, не платят, не трогают токены). Они только:
  - ManualAdapter: кладут поручение в inbox домика проекта (внутри его sandbox)
    и возвращают расписку. Реальную работу выполняет агент проекта, когда вы его
    откроете. Это безопасно и полностью работает офлайн.
  - CrossSessionAdapter: описывает передачу задачи в живую сессию Claude Code
    через штатные ListAgents/SendMessage. Сама передача возможна только на вашем
    компьютере при подключённом Remote Control и онлайн-сессии проекта. Если
    возможности нет, адаптер честно возвращает not_available и не выдумывает ответ.

cross-session messaging передаёт только текст и не гарантирует ответ обратно,
поэтому это механизм поручения, а не удалённый вызов с гарантированным результатом.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .registry import Project
from .runner import ClaudeCodeAdapter, ExecContext, ExecResult, ProcessAdapter

# Виды исхода доставки.
QUEUED_LOCAL = "queued_local"        # поручение записано в домик проекта
HANDOFF_READY = "handoff_ready"      # готова инструкция передачи в сессию
NOT_AVAILABLE = "not_available"      # передача сейчас невозможна (нет доступа)
UNSUPPORTED = "unsupported"          # неизвестный тип адаптера


@dataclass
class DeliveryResult:
    delivered: bool
    kind: str
    message: str
    receipt: Dict = field(default_factory=dict)


class Adapter:
    def deliver(self, project: Project, task: Dict) -> DeliveryResult:  # pragma: no cover
        raise NotImplementedError


class ManualAdapter(Adapter):
    """Записывает поручение в inbox домика проекта. Ничего не отправляет наружу."""

    def deliver(self, project: Project, task: Dict) -> DeliveryResult:
        sandbox = project.sandbox(create=True)
        record = {
            "id": task.get("id"),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "text": task.get("text", ""),
            "from_user": task.get("user_id"),
            "status": "queued",
        }
        # Путь строится через sandbox, значит гарантированно внутри домика.
        rel = "inbox/tasks.jsonl"
        sandbox.append_line(rel, json.dumps(record, ensure_ascii=False))
        return DeliveryResult(
            delivered=True,
            kind=QUEUED_LOCAL,
            message=(
                f"Поручение записано в домик проекта «{project.title}» "
                f"(inbox/tasks.jsonl). Его выполнит агент проекта."
            ),
            receipt={"path": rel, "task_id": task.get("id")},
        )

    def execute(self, project: Project, task: Dict, ctx: ExecContext) -> ExecResult:
        self.deliver(project, task)
        # Запись в папку НЕ считается выполнением: executed=False.
        return ExecResult(
            ok=True, executed=False,
            output=("Поручение только записано в папку проекта (inbox/tasks.jsonl). "
                    "Агент этого проекта не запускается автоматически."),
        )


class CrossSessionAdapter(Adapter):
    """Готовит передачу задачи в живую сессию Claude Code проекта.

    Реальную отправку делает окружение Claude Code (ListAgents/SendMessage) на
    вашем компьютере. Здесь мы только проверяем доступность и формируем инструкцию,
    не имитируя ни отправку, ни ответ.
    """

    def __init__(self, cross_session_available: bool = False) -> None:
        self.available = cross_session_available

    def deliver(self, project: Project, task: Dict) -> DeliveryResult:
        target = project.adapter.get("target_session_name") or project.adapter.get(
            "target_session_id"
        )
        if not target:
            return DeliveryResult(
                delivered=False,
                kind=NOT_AVAILABLE,
                message=(
                    f"Для проекта «{project.title}» не задана целевая сессия "
                    f"(target_session_name) в манифесте."
                ),
            )
        if not self.available:
            return DeliveryResult(
                delivered=False,
                kind=NOT_AVAILABLE,
                message=(
                    f"Передача в сессию «{target}» сейчас недоступна: нужен "
                    f"запуск на вашем компьютере с подключённым Remote Control "
                    f"и онлайн-сессией проекта. Задача не отправлена."
                ),
                receipt={"target": target},
            )
        # Возможность есть: возвращаем готовую инструкцию передачи. Фактическую
        # отправку выполняет слой Claude Code, не этот код.
        return DeliveryResult(
            delivered=False,  # доставку подтвердит слой Claude Code, а не мы
            kind=HANDOFF_READY,
            message=f"Готово к передаче задачи в сессию «{target}» через SendMessage.",
            receipt={"target": target, "text": task.get("text", "")},
        )

    def execute(self, project: Project, task: Dict, ctx: ExecContext) -> ExecResult:
        res = self.deliver(project, task)
        return ExecResult(ok=False, executed=False, error=res.message)


def build_adapter(project: Project, *, cross_session_available: bool = False) -> Adapter:
    kind = (project.adapter or {}).get("type", "manual")
    if kind == "manual":
        return ManualAdapter()
    if kind == "cross_session":
        return CrossSessionAdapter(cross_session_available=cross_session_available)
    if kind == "process":
        return ProcessAdapter(project.adapter)
    if kind == "claude_code":
        return ClaudeCodeAdapter(project.adapter)
    # local_process и прочие типы на этапе 1 не реализованы: не притворяемся.
    return _UnsupportedAdapter(kind)


class _UnsupportedAdapter(Adapter):
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def deliver(self, project: Project, task: Dict) -> DeliveryResult:
        return DeliveryResult(
            delivered=False,
            kind=UNSUPPORTED,
            message=(
                f"Тип адаптера '{self.kind}' на этапе 1 не поддержан. "
                f"Задача не отправлена."
            ),
        )

    def execute(self, project: Project, task: Dict, ctx: ExecContext) -> ExecResult:
        return ExecResult(ok=False, executed=False, error=self.deliver(project, task).message)
