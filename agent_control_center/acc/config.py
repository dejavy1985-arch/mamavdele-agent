"""Загрузка конфигурации центра управления.

Конфиг центра (allowlist, пути, флаги возможностей) хранится в JSON.
Секреты (токен Telegram) берутся из переменных окружения, а не из файла,
и никогда не коммитятся.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class ControlCenterConfig:
    base_dir: str
    homes_dir: str
    var_dir: str
    allowed_user_ids: List[int] = field(default_factory=list)
    cross_session_available: bool = False
    telegram_token_env: str = "ACC_TELEGRAM_BOT_TOKEN"

    @property
    def audit_path(self) -> str:
        return os.path.join(self.var_dir, "audit.jsonl")

    @property
    def state_path(self) -> str:
        return os.path.join(self.var_dir, "state.json")

    def telegram_token(self):
        return os.environ.get(self.telegram_token_env)


def load_config(config_path: str) -> ControlCenterConfig:
    base_dir = os.path.dirname(os.path.abspath(config_path))
    data = {}
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)

    homes_dir = os.path.join(base_dir, data.get("homes_dir", "homes"))
    var_dir = os.path.join(base_dir, data.get("var_dir", "var"))
    os.makedirs(var_dir, exist_ok=True)

    return ControlCenterConfig(
        base_dir=base_dir,
        homes_dir=homes_dir,
        var_dir=var_dir,
        allowed_user_ids=list(data.get("allowed_user_ids", [])),
        cross_session_available=bool(data.get("cross_session_available", False)),
        telegram_token_env=data.get("telegram_token_env", "ACC_TELEGRAM_BOT_TOKEN"),
    )
