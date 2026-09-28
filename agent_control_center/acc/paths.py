"""Изоляция файлового доступа между домиками (техническая, а не текстовая).

Каждый проект живёт в своём домике (директории). Агент проекта получает
объект ProjectSandbox, привязанный только к корню своего домика. Любой путь,
который выводит за пределы этого корня, отклоняется до операции с диском.

Защита закрывает три вектора, которые важны заказчику:
  1. абсолютные пути (например, /etc/passwd);
  2. переходы вверх и в соседние домики (например, ../other/secrets);
  3. символические ссылки, ведущие наружу.

Граница безопасности проверяется по реальному пути (os.path.realpath),
который раскрывает и "..", и симлинки. Дополнительно входные пути с сегментом
".." и абсолютные пути отклоняются сразу, с понятным сообщением.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List


class PathEscapeError(ValueError):
    """Попытка выйти за пределы домика проекта."""


def _real(path: str) -> str:
    # realpath раскрывает симлинки в существующей части пути и нормализует "..".
    return os.path.realpath(path)


def _is_within(root_real: str, target_real: str) -> bool:
    """True, если target_real это сам корень или лежит строго внутри него."""
    if target_real == root_real:
        return True
    # Сравниваем по границе разделителя, чтобы /home/app-secret не считался
    # находящимся внутри /home/app.
    return target_real.startswith(root_real + os.sep)


def safe_resolve(home_root: str, relpath: str) -> str:
    """Вернуть безопасный абсолютный путь внутри домика или бросить исключение.

    Аргументы:
        home_root: корень домика проекта (директория этого проекта).
        relpath:   путь, заданный относительно корня домика.

    Возвращает абсолютный, нормализованный путь строго внутри home_root.
    """
    if relpath is None:
        raise PathEscapeError("Пустой путь недопустим")

    # Нулевой байт это классический приём обхода фильтров.
    if "\x00" in relpath:
        raise PathEscapeError("Путь содержит нулевой байт")

    # Абсолютные пути запрещены: домик всегда адресуется относительно себя.
    if os.path.isabs(relpath):
        raise PathEscapeError(f"Абсолютные пути запрещены: {relpath!r}")

    # Домашний тильда-префикс раскрылся бы в чужую директорию.
    if relpath.startswith("~"):
        raise PathEscapeError(f"Путь с '~' запрещён: {relpath!r}")

    # Явный запрет сегмента "..": строгая, читаемая ошибка ещё до работы с диском.
    parts = [p for p in relpath.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PathEscapeError(f"Переход вверх ('..') запрещён: {relpath!r}")

    root_real = _real(home_root)
    target = os.path.join(root_real, *parts) if parts else root_real
    target_real = _real(target)

    # Итоговая проверка по реальному пути ловит симлинки, ведущие наружу.
    if not _is_within(root_real, target_real):
        raise PathEscapeError(
            f"Путь выходит за пределы домика: {relpath!r} -> {target_real}"
        )
    return target_real


class ProjectSandbox:
    """Файловый доступ, ограниченный корнем одного домика.

    Объект создаётся отдельно для каждого проекта и никогда не делится между
    проектами. Так один агент конструктивно не может прочитать или изменить
    файлы другого: у него просто нет sandbox с чужим корнем.
    """

    def __init__(self, home_root: str, create: bool = True) -> None:
        root = Path(home_root)
        if create:
            root.mkdir(parents=True, exist_ok=True)
        if not root.exists():
            raise FileNotFoundError(f"Домик не найден: {home_root}")
        # Храним реальный путь корня, чтобы сравнение границ было устойчивым.
        self.root_real = _real(str(root))

    # -- разрешение пути ---------------------------------------------------
    def resolve(self, relpath: str) -> str:
        return safe_resolve(self.root_real, relpath)

    def contains(self, relpath: str) -> bool:
        try:
            self.resolve(relpath)
            return True
        except PathEscapeError:
            return False

    # -- операции чтения/записи (все проходят через resolve) ---------------
    def read_text(self, relpath: str, encoding: str = "utf-8") -> str:
        return Path(self.resolve(relpath)).read_text(encoding=encoding)

    def write_text(self, relpath: str, data: str, encoding: str = "utf-8") -> str:
        target = self.resolve(relpath)
        parent = os.path.dirname(target)
        # Родитель тоже обязан лежать внутри домика (защита от симлинка-родителя).
        if not _is_within(self.root_real, _real(parent)):
            raise PathEscapeError(f"Родительская папка вне домика: {relpath!r}")
        os.makedirs(parent, exist_ok=True)
        # Отказываемся писать сквозь существующий симлинк.
        if os.path.islink(target):
            raise PathEscapeError(f"Запись через символическую ссылку запрещена: {relpath!r}")
        Path(target).write_text(data, encoding=encoding)
        return target

    def append_line(self, relpath: str, line: str, encoding: str = "utf-8") -> str:
        target = self.resolve(relpath)
        parent = os.path.dirname(target)
        if not _is_within(self.root_real, _real(parent)):
            raise PathEscapeError(f"Родительская папка вне домика: {relpath!r}")
        os.makedirs(parent, exist_ok=True)
        if os.path.islink(target):
            raise PathEscapeError(f"Запись через символическую ссылку запрещена: {relpath!r}")
        with open(target, "a", encoding=encoding) as fh:
            fh.write(line if line.endswith("\n") else line + "\n")
        return target

    def exists(self, relpath: str) -> bool:
        try:
            return os.path.exists(self.resolve(relpath))
        except PathEscapeError:
            return False

    def mkdir(self, relpath: str) -> str:
        target = self.resolve(relpath)
        os.makedirs(target, exist_ok=True)
        return target

    def list_dir(self, relpath: str = "") -> List[str]:
        target = self.resolve(relpath) if relpath else self.root_real
        return sorted(os.listdir(target))


def assert_isolated(sandboxes: Iterable[ProjectSandbox]) -> None:
    """Проверка на этапе загрузки: домики не вложены друг в друга.

    Если корень одного домика лежит внутри другого, изоляция потеряла бы смысл.
    Бросает PathEscapeError при пересечении.
    """
    roots = [sb.root_real for sb in sandboxes]
    for i, a in enumerate(roots):
        for j, b in enumerate(roots):
            if i == j:
                continue
            if _is_within(a, b) or a == b:
                raise PathEscapeError(
                    f"Домики пересекаются и нарушают изоляцию: {b} внутри {a}"
                )
