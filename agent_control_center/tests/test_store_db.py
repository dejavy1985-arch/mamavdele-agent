"""Тесты SQLite-хранилища: очередь, статусы, история, восстановление."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acc import store_db  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "acc.db")
        self.store = store_db.Store(self.path)

    def tearDown(self):
        self.store.close()

    def test_enqueue_and_next(self):
        self.store.enqueue_task("p1", "первое")
        self.store.enqueue_task("p1", "второе")
        nxt = self.store.next_queued("p1")
        self.assertEqual(nxt["text"], "первое")  # очередь по времени создания

    def test_status_lifecycle(self):
        t = self.store.enqueue_task("p1", "задача")
        self.assertEqual(t["status"], store_db.QUEUED)
        self.store.set_task_status(t["id"], store_db.PROCESSING)
        self.store.set_task_status(t["id"], store_db.DONE, result="готово")
        got = self.store.get_task(t["id"])
        self.assertEqual(got["status"], store_db.DONE)
        self.assertEqual(got["result"], "готово")

    def test_confirmation_flow(self):
        t = self.store.enqueue_task("p1", "удали ветку", needs_confirmation=True)
        self.assertEqual(t["status"], store_db.AWAITING_CONFIRMATION)
        # Пока не подтверждено, в очередь на выполнение не попадает.
        self.assertIsNone(self.store.next_queued("p1"))
        self.store.confirm_task(t["id"])
        self.assertEqual(self.store.get_task(t["id"])["status"], store_db.QUEUED)

    def test_reject(self):
        t = self.store.enqueue_task("p1", "опасное", needs_confirmation=True)
        self.store.reject_task(t["id"])
        self.assertEqual(self.store.get_task(t["id"])["status"], store_db.REJECTED)

    def test_confirm_only_from_awaiting(self):
        t = self.store.enqueue_task("p1", "обычное")  # сразу queued
        self.assertIsNone(self.store.confirm_task(t["id"]))  # нечего подтверждать

    def test_history_and_events(self):
        self.store.record_event("task_routed", project_id="p1", detail="x")
        self.store.record_event("task_done", project_id="p1")
        hist = self.store.history(10)
        self.assertEqual(hist[0]["event"], "task_done")  # новые сверху

    def test_session_resume_handle(self):
        self.store.set_session("p1", "sess-123")
        self.assertEqual(self.store.get_session("p1"), "sess-123")

    def test_recovery_after_reopen(self):
        t = self.store.enqueue_task("p1", "переживи перезапуск")
        self.store.set_task_status(t["id"], store_db.PROCESSING)
        self.store.set_session("p1", "sess-abc")
        self.store.record_event("marker")
        self.store.close()
        # Симуляция перезапуска: новый объект, тот же файл.
        store2 = store_db.Store(self.path)
        try:
            got = store2.get_task(t["id"])
            self.assertEqual(got["status"], store_db.PROCESSING)
            self.assertEqual(store2.get_session("p1"), "sess-abc")
            self.assertTrue(any(e["event"] == "marker" for e in store2.history(10)))
        finally:
            store2.close()

    def test_upsert_project_snapshot(self):
        self.store.upsert_project("p1", "Проект 1", "registered")
        self.store.upsert_project("p1", "Проект 1", "connected")  # обновление
        projs = self.store.projects()
        self.assertEqual(len(projs), 1)
        self.assertEqual(projs[0]["status"], "connected")


if __name__ == "__main__":
    unittest.main()
