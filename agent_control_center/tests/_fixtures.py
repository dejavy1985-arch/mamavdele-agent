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


# -- домики с настоящим тестовым агентом --------------------------------------
import shutil  # noqa: E402

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_SRC = os.path.join(PKG_ROOT, "homes", "test-agent", "agent.py")


def make_agent_home(homes_dir, pid, keywords=None, title=None, sandbox="preferred",
                    adapter_extra=None, status="connected"):
    """Временный домик, где работает настоящий тестовый агент (процесс, не ИИ)."""
    home = os.path.join(homes_dir, pid)
    os.makedirs(os.path.join(home, "secrets"), exist_ok=True)
    shutil.copy(AGENT_SRC, os.path.join(home, "agent.py"))
    adapter = {"type": "process", "command": ["{python}", "agent.py"],
               "sandbox": sandbox, "network": False, "timeout_sec": 30}
    adapter.update(adapter_extra or {})
    with open(os.path.join(home, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"id": pid, "title": title or pid, "status": status, "test": True,
                   "keywords": keywords or [pid], "adapter": adapter}, fh, ensure_ascii=False)
    return home


def build_agent_center(agents, allowed_user_ids=(111,), max_parallel=2):
    """Центр с временными домиками-агентами. agents: [(id, [ключевые слова]), ...]."""
    config, base = build_center(manifests=[], allowed_user_ids=allowed_user_ids)
    for pid, keywords in agents:
        make_agent_home(config.homes_dir, pid, keywords)
    config.max_parallel = max_parallel
    return config, base
