import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import dispatcher as disp  # noqa: E402
from acc.dispatcher import Dispatcher  # noqa: E402


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.config, self.base = _fixtures.build_center(allowed_user_ids=(111,))
        self.d = Dispatcher(self.config)

    # -- allowlist ----------------------------------------------------------
    def test_stranger_denied_and_silent(self):
        r = self.d.handle(999, "проверь комментарии")
        self.assertEqual(r.type, disp.DENIED)
        self.assertTrue(r.silent)

    # -- границы ------------------------------------------------------------
    def test_forbidden_action_not_executed(self):
        r = self.d.handle(111, "опубликуй пост в инстаграме")
        self.assertEqual(r.type, disp.BOUNDARY_BLOCKED)

    # -- маршрутизация ------------------------------------------------------
    def test_routes_to_moderation_and_queues(self):
        r = self.d.handle(111, "проверь комментарий в инстаграме")
        self.assertEqual(r.type, disp.ROUTED)
        self.assertEqual(r.project_id, "mamavdele-agent")
        self.assertTrue(r.data["delivered"])
        inbox = os.path.join(self.config.homes_dir, "mamavdele-agent", "inbox", "tasks.jsonl")
        self.assertTrue(os.path.exists(inbox))

    def test_clarify_when_ambiguous(self):
        r = self.d.handle(111, "комментарий и видео")
        self.assertEqual(r.type, disp.CLARIFY)
        self.assertEqual(set(r.data["candidates"]), {"mamavdele-agent", "content-studio"})

    def test_unknown_project(self):
        r = self.d.handle(111, "какая сегодня погода")
        self.assertEqual(r.type, disp.UNKNOWN)

    def test_not_connected_reported_honestly(self):
        manifests = _fixtures.default_manifests()
        manifests[1]["status"] = "not_connected"
        config, _ = _fixtures.build_center(manifests=manifests, allowed_user_ids=(111,))
        d = Dispatcher(config)
        r = d.handle(111, "сделай промпт для сцены видео seedance")
        self.assertEqual(r.type, disp.NOT_CONNECTED)
        self.assertEqual(r.project_id, "content-studio")

    # -- команды ------------------------------------------------------------
    def test_projects_command(self):
        r = self.d.handle(111, "/projects")
        self.assertEqual(r.type, disp.COMMAND)
        self.assertIn("Мамавделе", r.message)

    def test_status_command(self):
        r = self.d.handle(111, "/status")
        self.assertEqual(r.type, disp.COMMAND)
        self.assertIn("подключено", r.message)

    # -- восстановление журнала --------------------------------------------
    def test_actions_are_logged(self):
        self.d.handle(111, "проверь комментарий в инстаграме")
        events = self.d.audit.tail(20)
        self.assertTrue(any(e.get("event") == "task_routed" for e in events))


if __name__ == "__main__":
    unittest.main()
