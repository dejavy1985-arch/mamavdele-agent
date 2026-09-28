"""Тесты загрузки конфига: пути привязаны к корню проекта, проект переносим."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acc.config import load_config  # noqa: E402


class ConfigPathTests(unittest.TestCase):
    def test_paths_resolve_to_project_root_not_config_dir(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "config"))
        os.makedirs(os.path.join(root, "homes"))
        cfg_path = os.path.join(root, "config", "control_center.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"homes_dir": "homes", "var_dir": "var", "allowed_user_ids": [1]}, fh)

        config = load_config(cfg_path)
        # homes и var должны считаться от корня проекта, а не от папки config/.
        self.assertEqual(config.homes_dir, os.path.join(root, "homes"))
        self.assertEqual(config.var_dir, os.path.join(root, "var"))
        self.assertEqual(config.base_dir, root)
        self.assertTrue(os.path.isdir(config.var_dir))  # создаётся автоматически

    def test_token_from_file_when_no_env(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "config"))
        cfg_path = os.path.join(root, "config", "control_center.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        with open(os.path.join(root, "config", "telegram_token.txt"), "w", encoding="utf-8") as fh:
            fh.write("  secret-token  \n")
        config = load_config(cfg_path)
        os.environ.pop(config.telegram_token_env, None)
        self.assertEqual(config.telegram_token(), "secret-token")

    def test_absolute_paths_in_config_respected(self):
        root = tempfile.mkdtemp()
        homes = tempfile.mkdtemp()
        cfg_path = os.path.join(root, "control_center.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"homes_dir": homes}, fh)
        config = load_config(cfg_path)
        self.assertEqual(config.homes_dir, homes)


if __name__ == "__main__":
    unittest.main()
