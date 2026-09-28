import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import adapters  # noqa: E402
from acc.registry import Registry  # noqa: E402


class ManualAdapterTests(unittest.TestCase):
    def setUp(self):
        self.config, _ = _fixtures.build_center()
        self.reg = Registry(self.config.homes_dir).load()

    def test_task_written_into_own_home_only(self):
        project = self.reg.get("content-studio")
        adapter = adapters.build_adapter(project)
        res = adapter.deliver(project, {"id": "abc", "text": "сцена 1", "user_id": 111})
        self.assertTrue(res.delivered)
        self.assertEqual(res.kind, adapters.QUEUED_LOCAL)
        # Файл появился в домике content-studio и НЕ появился в чужом домике.
        cs_inbox = os.path.join(self.config.homes_dir, "content-studio", "inbox", "tasks.jsonl")
        mv_inbox = os.path.join(self.config.homes_dir, "mamavdele-agent", "inbox", "tasks.jsonl")
        self.assertTrue(os.path.exists(cs_inbox))
        self.assertFalse(os.path.exists(mv_inbox))


class CrossSessionAdapterTests(unittest.TestCase):
    def _project(self, available):
        manifests = [{
            "id": "content-studio",
            "title": "Студия",
            "status": "connected",
            "keywords": ["видео"],
            "adapter": {"type": "cross_session", "target_session_name": "Водяной"},
        }]
        config, _ = _fixtures.build_center(
            manifests=manifests, cross_session_available=available
        )
        return config, Registry(config.homes_dir).load().get("content-studio")

    def test_not_available_is_honest(self):
        config, project = self._project(available=False)
        adapter = adapters.build_adapter(project, cross_session_available=False)
        res = adapter.deliver(project, {"id": "x", "text": "t"})
        self.assertFalse(res.delivered)
        self.assertEqual(res.kind, adapters.NOT_AVAILABLE)

    def test_handoff_ready_when_available(self):
        config, project = self._project(available=True)
        adapter = adapters.build_adapter(project, cross_session_available=True)
        res = adapter.deliver(project, {"id": "x", "text": "t"})
        self.assertEqual(res.kind, adapters.HANDOFF_READY)
        # Доставку подтверждает слой Claude Code, а не наш код: delivered=False.
        self.assertFalse(res.delivered)

    def test_unsupported_adapter_type(self):
        manifests = [{
            "id": "p", "title": "P", "status": "connected",
            "keywords": ["x"], "adapter": {"type": "local_process"},
        }]
        config, _ = _fixtures.build_center(manifests=manifests)
        project = Registry(config.homes_dir).load().get("p")
        res = adapters.build_adapter(project).deliver(project, {"id": "1", "text": "t"})
        self.assertEqual(res.kind, adapters.UNSUPPORTED)
        self.assertFalse(res.delivered)


if __name__ == "__main__":
    unittest.main()
