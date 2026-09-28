"""Журнал действий и ошибок (append-only JSONL).

Каждое событие это одна строка JSON. Формат устойчив к перезапуску: файл только
дописывается, поэтому история сохраняется между запусками программы и компьютера.
Секреты сюда писать нельзя (тексты сообщений усечены, токены не логируются).
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

# Уровни для быстрого разделения обычных действий и ошибок.
INFO = "info"
WARNING = "warning"
ERROR = "error"

_MAX_TEXT = 500  # усечение пользовательского текста в журнале


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


class AuditLog:
    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def log(
        self,
        event: str,
        level: str = INFO,
        user_id: Optional[int] = None,
        project: Optional[str] = None,
        text: Optional[str] = None,
        **extra,
    ) -> dict:
        record = {
            "ts": _now_iso(),
            "level": level,
            "event": event,
        }
        if user_id is not None:
            record["user_id"] = user_id
        if project is not None:
            record["project"] = project
        if text is not None:
            record["text"] = text[:_MAX_TEXT]
        if extra:
            record.update(extra)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def tail(self, n: int = 20) -> list:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as fh:
            lines = fh.readlines()
        out = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
