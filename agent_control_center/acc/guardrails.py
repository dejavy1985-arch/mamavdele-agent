"""Границы первого этапа.

Центр управления на этапе 1 умеет: показывать проекты и их состояние,
маршрутизировать задачу нужному агенту (как черновик/поручение), вести журнал,
читать и дописывать собственную память. Он НЕ выполняет сам: публикацию контента,
переписку с клиентами, платежи, смену токенов/доступов/настроек агентов.

Это ограничение техническое, а не только текстовое:
  1. в коде центра нет ни одной функции, которая публикует, пишет клиентам,
     проводит платёж или меняет токены (см. adapters.py: адаптеры только
     передают задачу или читают статус, отправки во внешние сервисы нет);
  2. эта функция распознаёт запрос на такое действие и помечает решение
     как boundary_blocked, чтобы диспетчер не выполнял его молча и честно
     сообщил пользователю, что действие вне рамок этапа 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# Категории запрещённых на этапе 1 действий и их маркеры (по-русски и по-английски).
FORBIDDEN_PATTERNS = {
    "publish": [
        r"\bопубликуй", r"\bопубликовать", r"\bвыложи\b", r"\bвыложить\b",
        r"\bзапостить?\b", r"\bпубликац", r"\bpublish\b", r"\bpost\b",
    ],
    "message_clients": [
        r"напиши\s+клиент", r"ответь\s+клиент", r"отправь\s+клиент",
        r"напиши\s+в\s+директ", r"ответь\s+в\s+директ", r"отправь\s+сообщени",
        r"\bdm\b", r"message\s+client",
    ],
    "payments": [
        r"\bоплати\b", r"\bоплатить\b", r"\bплат[её]ж", r"\bперевед[иё]",
        r"\bперевод\b", r"выстави\s+сч[её]т", r"\bpay\b", r"\bpayment\b",
    ],
    "change_secrets": [
        r"смени\s+токен", r"поменяй\s+токен", r"обнови\s+токен",
        r"смени\s+ключ", r"поменяй\s+ключ", r"смени\s+доступ",
        r"поменяй\s+доступ", r"измени\s+настройк", r"change\s+token",
        r"rotate\s+(the\s+)?key", r"api[_\s-]?key",
    ],
}

_COMPILED = {
    cat: [re.compile(p, re.IGNORECASE) for p in pats]
    for cat, pats in FORBIDDEN_PATTERNS.items()
}

_HUMAN_NAMES = {
    "publish": "публикация контента",
    "message_clients": "переписка с клиентами",
    "payments": "платежи",
    "change_secrets": "смена токенов, доступов или настроек агентов",
}


@dataclass
class BoundaryCheck:
    blocked: bool
    categories: List[str]

    def human(self) -> str:
        return ", ".join(_HUMAN_NAMES.get(c, c) for c in self.categories)


def check_boundaries(text: str) -> BoundaryCheck:
    """Определить, просит ли текст выполнить запрещённое на этапе 1 действие."""
    text = text or ""
    hit = []
    for cat, patterns in _COMPILED.items():
        if any(p.search(text) for p in patterns):
            hit.append(cat)
    return BoundaryCheck(blocked=bool(hit), categories=hit)
