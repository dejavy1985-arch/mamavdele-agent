"""Allowlist: центр принимает команды только от разрешённых Telegram-аккаунтов.

Проверяется числовой user_id отправителя (не username, который можно сменить).
Список хранится в конфиге центра. Пустой список означает "никому не разрешено"
(безопасное значение по умолчанию), чтобы забытый конфиг не открыл доступ всем.
"""

from __future__ import annotations

from typing import Iterable, List


class Allowlist:
    def __init__(self, allowed_user_ids: Iterable[int]) -> None:
        # Приводим к int и множеству: защищаемся от строк в конфиге.
        self._allowed = set()
        for uid in allowed_user_ids or []:
            try:
                self._allowed.add(int(uid))
            except (TypeError, ValueError):
                continue

    def is_allowed(self, user_id) -> bool:
        try:
            return int(user_id) in self._allowed
        except (TypeError, ValueError):
            return False

    def ids(self) -> List[int]:
        return sorted(self._allowed)

    def __len__(self) -> int:
        return len(self._allowed)
