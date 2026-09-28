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
        # Сначала переменная окружения, затем локальный файл (для Windows без export).
        # Файлы токена не попадают в git (см. .gitignore). base_dir это корень проекта.
        env_token = os.environ.get(self.telegram_token_env)
        if env_token:
            return env_token.strip()
        candidates = [
            os.path.join(self.base_dir, "config", "telegram_token.txt"),
            os.path.join(self.base_dir, "telegram_token.txt"),
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        value = fh.read().strip()
                    if value:
                        return value
                except OSError:
                    continue
        return None


def load_config(config_path: str) -> ControlCenterConfig:
    """Загрузить конфиг. Пути homes/var считаются от КОРНЯ проекта, а не от папки
    config/, поэтому проект можно перенести в любую папку без правок кода."""
    config_path = os.path.abspath(config_path)
    config_dir = os.path.dirname(config_path)
    # Если конфиг лежит в папке config/, корень проекта это её родитель.
    if os.path.basename(config_dir) == "config":
        base_dir = os.path.dirname(config_dir)
    else:
        base_dir = config_dir

    data = {}
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)

    def _resolve(rel: str) -> str:
        return rel if os.path.isabs(rel) else os.path.join(base_dir, rel)

    homes_dir = _resolve(data.get("homes_dir", "homes"))
    var_dir = _resolve(data.get("var_dir", "var"))
    os.makedirs(var_dir, exist_ok=True)

    return ControlCenterConfig(
        base_dir=base_dir,
        homes_dir=homes_dir,
        var_dir=var_dir,
        allowed_user_ids=list(data.get("allowed_user_ids", [])),
        cross_session_available=bool(data.get("cross_session_available", False)),
        telegram_token_env=data.get("telegram_token_env", "ACC_TELEGRAM_BOT_TOKEN"),
    )
