"""Помощники для тестов: собирают временный центр с домиками."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acc.config import ControlCenterConfig  # noqa: E402


def make_home(homes_dir, manifest):
    home = os.path.join(homes_dir, manifest["id"])
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False)
    return home


def default_manifests():
    return [
        {
            "id": "mamavdele-agent",
            "title": "Мамавделе: модерация",
            "status": "connected",
            "keywords": ["инстаграм", "instagram", "комментарий", "модерация"],
            "aliases": ["мамавделе"],
            "adapter": {"type": "manual"},
        },
        {
            "id": "content-studio",
            "title": "Контент-студия: видео",
            "status": "connected",
            "keywords": ["видео", "ролик", "сцена", "seedance"],
            "aliases": ["студия"],
            "adapter": {"type": "manual"},
        },
    ]


def build_center(manifests=None, allowed_user_ids=(111,), cross_session_available=False):
    """Создать временный центр и вернуть (config, base_dir)."""
    base = tempfile.mkdtemp()
    homes_dir = os.path.join(base, "homes")
    var_dir = os.path.join(base, "var")
    os.makedirs(homes_dir, exist_ok=True)
    os.makedirs(var_dir, exist_ok=True)
    for m in (manifests if manifests is not None else default_manifests()):
        make_home(homes_dir, m)
    config = ControlCenterConfig(
        base_dir=base,
        homes_dir=homes_dir,
        var_dir=var_dir,
        allowed_user_ids=list(allowed_user_ids),
        cross_session_available=cross_session_available,
    )
    return config, base
