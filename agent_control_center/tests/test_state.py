"""Восстановление после перезапуска: новый экземпляр State читает тот же файл."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acc.state import State  # noqa: E402


class StateRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "state.json")

    def test_offset_survives_restart(self):
        s1 = State(self.path)
        s1.set_telegram_offset(42)
        # Симулируем перезапуск: новый объект, тот же файл.
        s2 = State(self.path)
        self.assertEqual(s2.telegram_offset, 42)

    def test_pending_tasks_survive_restart(self):
        s1 = State(self.path)
        s1.add_pending({"id": "t1", "text": "a"})
        s1.add_pending({"id": "t2", "text": "b"})
        s1.resolve_pending("t1")
        s2 = State(self.path)
        ids = [t["id"] for t in s2.pending_tasks()]
        self.assertEqual(ids, ["t2"])

    def test_project_status_survives_restart(self):
        s1 = State(self.path)
        s1.set_project_status("content-studio", "not_connected")
        s2 = State(self.path)
        self.assertEqual(s2.project_status()["content-studio"], "not_connected")

    def test_corrupt_file_does_not_crash(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ это не json ")
        s = State(self.path)  # не должно упасть
        self.assertEqual(s.telegram_offset, 0)


if __name__ == "__main__":
    unittest.main()
