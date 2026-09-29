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
# Это второй слой. Первый и главный: агентам не выдаются ключи от соцсетей, почты,
# мессенджеров клиентов и платёжных сервисов, поэтому выполнить такое действие им
# технически нечем. Маркеры ловят явную просьбу, чтобы честно ответить владельцу, и
# не должны срабатывать на обычные слова («перевод текста», «черновик публикации»).
_SEND_VERBS = r"(напиши|ответь|отправь|скинь|перешли|пришли)"
FORBIDDEN_PATTERNS = {
    "publish": [
        r"\bопубликуй", r"\bопубликовать", r"\bвыложи\b", r"\bвыложить\b",
        r"\bзапост", r"\bпостни", r"\bразмести(те)?\s+(это\s+|его\s+|её\s+|пост\s+)?в\s+(ленте|ленту|канал|сторис|инст|телеграм|соцсет)",
        r"\bpublish\b", r"\bpost\s+(it|this|to|on)\b",
    ],
    "message_clients": [
        r"напиши\s+клиент", r"ответь\s+клиент", r"отправь\s+клиент",
        _SEND_VERBS + r"\b.{0,40}\b(в\s+директ|в\s+личку|в\s+лс\b|в\s+личные)",
        r"\bотправь\s+(сообщени|письм)", r"\bdm\b", r"message\s+client",
        r"\bsend\s+(an?\s+)?(email|message)",
    ],
    "payments": [
        r"\bоплати\b", r"\bоплатить\b", r"\bплат[её]ж",
        r"\bперевед[иё]\w*\s+(\d|деньг|денег|сумм|оплат|на\s+карт|на\s+сч[её]т)",
        r"\bперевод\s+(денег|средств|на\s+карт)",
        r"выстави\s+сч[её]т", r"\bpay\b", r"\bpayment\b",
    ],
    "change_secrets": [
        r"\b(смени|поменяй|обнови|замени|сбрось)\w*\s+.{0,20}(токен|ключ|доступ|парол)",
        r"измени\s+настройк", r"\b(change|rotate|replace)\s+.{0,20}(token|key|password)",
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


# Опасные, но НЕ запрещённые действия: их можно выполнить, но только после явного
# подтверждения пользователя. Это аналог Plan Mode и подтверждения инструментов в
# Jarvis: рискованное действие сначала показывается, потом выполняется.
RISKY_PATTERNS = {
    "delete_files": [
        r"\bудали\b", r"\bудалить\b", r"\bснеси\b", r"\bсотри\b",
        r"rm\s+-rf", r"\bdelete\b", r"\bremove\b", r"\bdrop\s+table",
        r"\bdrop\s+database",
    ],
    "overwrite": [
        r"\bперезапиши\b", r"\bперезаписать\b", r"\bзатри\b", r"overwrite",
        r"git\s+reset\s+--hard", r"force\s+push", r"push\s+--force", r"--force\b",
    ],
    "deploy": [
        r"\bдеплой", r"\bвыкати\b", r"\bвыкатить\b", r"\bразверни\s+на\s+прод",
        r"\bdeploy\b", r"\bproduction\b", r"\bна\s+прод\b",
    ],
    "bulk": [
        r"\bвсе\s+файлы\b", r"\bмассово\b", r"\bпо\s+всем\b", r"\ball\s+files\b",
    ],
}

_COMPILED_RISKY = {
    cat: [re.compile(p, re.IGNORECASE) for p in pats]
    for cat, pats in RISKY_PATTERNS.items()
}

_RISKY_HUMAN = {
    "delete_files": "удаление файлов или данных",
    "overwrite": "перезапись или сброс изменений",
    "deploy": "выкатка или деплой",
    "bulk": "массовое изменение",
}


@dataclass
class RiskCheck:
    risky: bool
    categories: List[str]

    def human(self) -> str:
        return ", ".join(_RISKY_HUMAN.get(c, c) for c in self.categories)


def check_risky(text: str) -> RiskCheck:
    """Определить, требует ли действие подтверждения (опасное, но разрешённое)."""
    text = text or ""
    hit = []
    for cat, patterns in _COMPILED_RISKY.items():
        if any(p.search(text) for p in patterns):
            hit.append(cat)
    return RiskCheck(risky=bool(hit), categories=hit)
