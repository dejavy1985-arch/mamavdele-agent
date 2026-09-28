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

    def test_claim_next_one_task_per_project(self):
        a1 = self.store.enqueue_task("a", "1")
        a2 = self.store.enqueue_task("a", "2")
        b1 = self.store.enqueue_task("b", "1")
        first = self.store.claim_next()
        self.assertEqual(first["id"], a1["id"])
        self.assertEqual(first["status"], store_db.PROCESSING)
        self.assertIsNotNone(first["started_at"])
        # Проект a занят: следующей берётся задача проекта b, а не a2.
        self.assertEqual(self.store.claim_next({"a"})["id"], b1["id"])
        self.assertIsNone(self.store.claim_next({"a", "b"}))
        self.assertEqual(self.store.count_ahead(a2), 1)

    def test_reply_address_and_finish_time_stored(self):
        t = self.store.enqueue_task("a", "x", chat_id=5, reply_to=9)
        self.store.set_task_status(t["id"], store_db.DONE, result="ok")
        got = self.store.get_task(t["id"])
        self.assertEqual((got["chat_id"], got["reply_to"]), (5, 9))
        self.assertIsNotNone(got["finished_at"])

    def test_old_database_is_migrated(self):
        import sqlite3
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT, text TEXT, "
                     "status TEXT, needs_confirmation INTEGER, created_at TEXT, "
                     "updated_at TEXT, result TEXT)")
        conn.execute("INSERT INTO tasks VALUES ('old1','p','старая','queued',0,'x','x',NULL)")
        conn.commit()
        conn.close()
        st = store_db.Store(path)
        try:
            self.assertEqual(st.get_task("old1")["text"], "старая")   # данные на месте
            self.assertIn("chat_id", st.get_task("old1"))               # новые колонки есть
        finally:
            st.close()

    def test_upsert_project_snapshot(self):
        self.store.upsert_project("p1", "Проект 1", "registered")
        self.store.upsert_project("p1", "Проект 1", "connected")  # обновление
        projs = self.store.projects()
        self.assertEqual(len(projs), 1)
        self.assertEqual(projs[0]["status"], "connected")


if __name__ == "__main__":
    unittest.main()
