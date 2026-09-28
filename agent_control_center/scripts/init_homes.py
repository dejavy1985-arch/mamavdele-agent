#!/usr/bin/env python3
"""Идемпотентная подготовка домиков: создаёт недостающие папки inbox/memory/secrets.

Ничего не перезаписывает и не удаляет. Безопасно запускать повторно.

    python3 scripts/init_homes.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, BASE)

from acc.registry import Registry  # noqa: E402


def main() -> int:
    homes_dir = os.path.join(BASE, "homes")
    reg = Registry(homes_dir).load()
    for project in reg.all():
        for sub in ("inbox", "memory", "secrets"):
            path = os.path.join(project.home_dir, sub)
            os.makedirs(path, exist_ok=True)
        print(f"OK: {project.id} ({project.status})")
    print(f"Готово. Домиков: {len(reg.all())}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
