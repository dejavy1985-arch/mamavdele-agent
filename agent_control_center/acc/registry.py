"""Реестр домиков (проектов).

Каждый домик самоописателен: в его папке лежит manifest.json. Реестр сканирует
директорию homes/, читает манифесты, проверяет их и отдаёт список проектов.
Так добавление проекта это добавление папки с манифестом, без правки кода.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .paths import ProjectSandbox, assert_isolated

# Статусы домика.
STATUS_CONNECTED = "connected"        # проект подключён и готов принимать задачи
STATUS_REGISTERED = "registered"      # домик заведён, но живой агент ещё не привязан
STATUS_NOT_CONNECTED = "not_connected"  # проект известен, но сейчас недоступен
STATUS_PAUSED = "paused"              # временно выключен вручную

VALID_STATUSES = {
    STATUS_CONNECTED,
    STATUS_REGISTERED,
    STATUS_NOT_CONNECTED,
    STATUS_PAUSED,
}

REQUIRED_FIELDS = ("id", "title", "adapter")


@dataclass
class Project:
    id: str
    title: str
    home_dir: str
    summary: str = ""
    status: str = STATUS_REGISTERED
    keywords: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    adapter: Dict = field(default_factory=dict)
    allowed_actions: List[str] = field(default_factory=list)
    raw: Dict = field(default_factory=dict)

    def sandbox(self, create: bool = True) -> ProjectSandbox:
        return ProjectSandbox(self.home_dir, create=create)

    def is_connected(self) -> bool:
        return self.status == STATUS_CONNECTED


class RegistryError(ValueError):
    pass


def _validate_manifest(data: Dict, home_dir: str) -> None:
    for f in REQUIRED_FIELDS:
        if f not in data:
            raise RegistryError(f"В манифесте {home_dir} нет обязательного поля '{f}'")
    status = data.get("status", STATUS_REGISTERED)
    if status not in VALID_STATUSES:
        raise RegistryError(
            f"Недопустимый статус '{status}' в {home_dir}. "
            f"Разрешены: {sorted(VALID_STATUSES)}"
        )
    adapter = data.get("adapter", {})
    if not isinstance(adapter, dict) or "type" not in adapter:
        raise RegistryError(f"Поле adapter в {home_dir} должно быть объектом с 'type'")


def load_project(manifest_path: str) -> Project:
    home_dir = os.path.dirname(os.path.abspath(manifest_path))
    with open(manifest_path, encoding="utf-8") as fh:
        data = json.load(fh)
    _validate_manifest(data, home_dir)
    return Project(
        id=data["id"],
        title=data["title"],
        home_dir=home_dir,
        summary=data.get("summary", ""),
        status=data.get("status", STATUS_REGISTERED),
        keywords=[k.lower() for k in data.get("keywords", [])],
        aliases=[a.lower() for a in data.get("aliases", [])],
        adapter=data.get("adapter", {}),
        allowed_actions=data.get("allowed_actions", []),
        raw=data,
    )


class Registry:
    """Набор домиков, загруженных из директории homes/."""

    def __init__(self, homes_dir: str) -> None:
        self.homes_dir = os.path.abspath(homes_dir)
        self.projects: Dict[str, Project] = {}

    def load(self) -> "Registry":
        self.projects = {}
        if not os.path.isdir(self.homes_dir):
            return self
        for name in sorted(os.listdir(self.homes_dir)):
            manifest = os.path.join(self.homes_dir, name, "manifest.json")
            if os.path.isfile(manifest):
                project = load_project(manifest)
                if project.id in self.projects:
                    raise RegistryError(f"Дублируется id домика: {project.id}")
                self.projects[project.id] = project
        # Проверяем, что домики не вложены друг в друга (иначе изоляция сломана).
        assert_isolated([p.sandbox(create=False) for p in self.projects.values()])
        return self

    def all(self) -> List[Project]:
        return list(self.projects.values())

    def get(self, project_id: str) -> Optional[Project]:
        return self.projects.get(project_id)

    def connected(self) -> List[Project]:
        return [p for p in self.projects.values() if p.is_connected()]
