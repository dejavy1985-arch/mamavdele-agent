"""Состояние центра для восстановления после перезапуска.

Хранит: смещение обработанных сообщений Telegram (update_id), незавершённые
задачи и последнее известное состояние проектов. Запись атомарная (через
временный файл + rename), чтобы обрыв питания не оставил битый файл.

Восстановление это просто повторная загрузка того же файла при старте: новый
экземпляр State читает сохранённые данные и продолжает с той же точки.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, List


class State:
    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.data: Dict = {
            "telegram_offset": 0,   # следующий update_id, который нужно запросить
            "pending_tasks": [],    # задачи, принятые, но ещё не завершённые
            "project_status": {},   # последнее известное состояние проектов
        }
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (json.JSONDecodeError, OSError):
                # Битый файл не должен ронять старт: продолжаем с дефолтов.
                pass

    def _save(self) -> None:
        directory = os.path.dirname(self.path)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)  # атомарная замена
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # -- смещение Telegram --------------------------------------------------
    @property
    def telegram_offset(self) -> int:
        return int(self.data.get("telegram_offset", 0))

    def set_telegram_offset(self, offset: int) -> None:
        self.data["telegram_offset"] = int(offset)
        self._save()

    # -- незавершённые задачи ----------------------------------------------
    def add_pending(self, task: Dict) -> None:
        self.data.setdefault("pending_tasks", []).append(task)
        self._save()

    def resolve_pending(self, task_id: str) -> None:
        tasks = self.data.get("pending_tasks", [])
        self.data["pending_tasks"] = [t for t in tasks if t.get("id") != task_id]
        self._save()

    def pending_tasks(self) -> List[Dict]:
        return list(self.data.get("pending_tasks", []))

    # -- состояние проектов -------------------------------------------------
    def set_project_status(self, project_id: str, status: str) -> None:
        self.data.setdefault("project_status", {})[project_id] = status
        self._save()

    def project_status(self) -> Dict[str, str]:
        return dict(self.data.get("project_status", {}))
