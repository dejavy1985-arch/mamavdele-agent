"""Тесты командной строки (офлайн, без Telegram)."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import cli  # noqa: E402


def run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


class CliTests(unittest.TestCase):
    def test_doctor_ok(self):
        code, out = run(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("Домиков загружено", out)

    def test_route_dry_run(self):
        code, out = run(["route", "проверь комментарий в инстаграме"])
        self.assertEqual(code, 0)
        self.assertIn("Исход маршрутизации", out)

    def test_demo_runs_agents_and_isolates(self):
        code, out = run(["demo"])
        self.assertEqual(code, 0)
        self.assertIn("тевирп", out)                   # агент альфы реально выполнил
        self.assertIn("Слов: 3", out)                   # агент беты реально выполнил
        self.assertNotIn("ДА, изоляции нет", out)

    def _agent_config(self):
        import json
        config, base = _fixtures.build_agent_center([("test-agent", ["тест"])])
        os.makedirs(os.path.join(base, "config"), exist_ok=True)
        path = os.path.join(base, "config", "control_center.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"homes_dir": config.homes_dir, "var_dir": config.var_dir,
                       "allowed_user_ids": [111]}, fh)
        return path

    def test_ask_runs_full_path_without_telegram(self):
        code, out = run(["--config", self._agent_config(), "ask", "тест: переверни абв"])
        self.assertEqual(code, 0, out)
        self.assertIn("Принято", out)
        self.assertIn("вба", out)

    def test_send_respects_allowlist(self):
        code, out = run(["--config", self._agent_config(), "send", "--user", "999",
                         "тест: переверни абв"])
        self.assertEqual(code, 1)
        self.assertIn("allowlist", out)

    def test_add_home_scaffolds_registered(self):
        tmp_homes = tempfile.mkdtemp()
        original = cli.HOMES_DIR
        cli.HOMES_DIR = tmp_homes
        try:
            code, out = run(["add-home", "проба", "--title", "Проба", "--keywords", "a,b"])
            self.assertEqual(code, 0)
            manifest = os.path.join(tmp_homes, "проба", "manifest.json")
            self.assertTrue(os.path.exists(manifest))
            import json
            with open(manifest, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["status"], "registered")  # не connected
        finally:
            cli.HOMES_DIR = original

    def test_add_home_refuses_duplicate(self):
        tmp_homes = tempfile.mkdtemp()
        original = cli.HOMES_DIR
        cli.HOMES_DIR = tmp_homes
        try:
            run(["add-home", "dup", "--title", "Dup"])
            code, out = run(["add-home", "dup", "--title", "Dup"])
            self.assertEqual(code, 1)
            self.assertIn("уже существует", out)
        finally:
            cli.HOMES_DIR = original


if __name__ == "__main__":
    unittest.main()
