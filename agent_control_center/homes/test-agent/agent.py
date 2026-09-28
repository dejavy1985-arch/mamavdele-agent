#!/usr/bin/env python3
"""ТЕСТОВЫЙ агент Клопа. Не реальный проект и не ИИ.

Нужен, чтобы проверить весь путь: задача из Telegram, агент реально выполняет её
в своей папке, результат возвращается в тот же чат. Текст задачи приходит на stdin,
ответ агент печатает в stdout.

Что умеет (по словам в задаче):
  переверни <текст>        перевернуть текст
  посчитай слова <текст>   сколько слов
  запомни <текст>          сохранить заметку в notes.txt своей папки
  заметки                  показать сохранённые заметки
  подожди <N>              подождать N секунд (проверка очереди и /cancel)
  сломайся                 завершиться с ошибкой (проверка неудачи)
  прочитай <путь>          попытаться прочитать файл (проверка изоляции)
  окружение                имена переменных окружения, без значений
Иначе записывает задачу в журнал своей папки (runs.log) и сообщает об этом.
"""

import os
import re
import sys
import time
from pathlib import Path


def after(word: str, text: str) -> str:
    i = text.lower().find(word)
    return text[i + len(word):].strip(" :,.") if i >= 0 else ""


def main() -> int:
    task = sys.stdin.read().strip()
    low = task.lower()
    home = Path.cwd()
    with open(home / "runs.log", "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                 f"{os.environ.get('ACC_TASK_ID', '?')} {task}\n")

    if "переверни" in low:
        s = after("переверни", task)
        print(s[::-1] if s else "Нечего переворачивать.")
        return 0

    if "посчитай слова" in low:
        s = after("посчитай слова", task)
        print(f"Слов: {len(s.split())}")
        return 0

    if "запомни" in low:
        s = after("запомни", task)
        notes = home / "notes.txt"
        with open(notes, "a", encoding="utf-8") as fh:
            fh.write(s + "\n")
        count = len(notes.read_text(encoding="utf-8").splitlines())
        print(f"Запомнил. Заметок в папке проекта: {count}.")
        return 0

    if "заметки" in low:
        notes = home / "notes.txt"
        lines = notes.read_text(encoding="utf-8").splitlines() if notes.exists() else []
        print("Заметки:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1))
              if lines else "Заметок пока нет.")
        return 0

    m = re.search(r"подожди\s+(\d+)", low)
    if m:
        n = min(int(m.group(1)), 600)
        time.sleep(n)
        print(f"Подождал {n} с.")
        return 0

    if "сломайся" in low:
        print("Тестовая ошибка по запросу.", file=sys.stderr)
        return 2

    if "прочитай" in low:
        path = after("прочитай", task)
        try:
            data = Path(path).read_text(encoding="utf-8")[:300]
            print(f"Прочитал {path}: {data}")
        except Exception as exc:
            print(f"Не могу прочитать {path}: {type(exc).__name__}")
        return 0

    if "окружение" in low:
        print("Переменные окружения агента: " + ", ".join(sorted(os.environ)))
        return 0

    print(f"Выполнил тестовую задачу: записал «{task}» в журнал своей папки "
          f"(runs.log). Это тестовый агент без ИИ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
