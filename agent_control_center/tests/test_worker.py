"""Тесты исполнителя: фоновое выполнение, статусы и результат в тот же чат."""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import dispatcher as disp  # noqa: E402
from acc import store_db  # noqa: E402
from acc.dispatcher import Dispatcher  # noqa: E402
from acc.worker import Worker  # noqa: E402


class Chat:
    """Поддельный чат: собирает всё, что исполнитель отправил бы в Telegram."""

    def __init__(self):
        self.messages = []
        self.lock = threading.Lock()

    def __call__(self, chat_id, text, reply_to=None):
        with self.lock:
            self.messages.append((chat_id, reply_to, text, time.monotonic()))

    def texts(self):
        return [m[2] for m in self.messages]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.config, _ = _fixtures.build_agent_center(
            [("alpha", ["альфа"]), ("beta", ["бета"])], max_parallel=2)
        self.d = Dispatcher(self.config)
        self.chat = Chat()
        self.w = Worker(self.d, notifier=self.chat, max_parallel=2)

    def tearDown(self):
        self.w.stop()
        self.d.store.close()

    def ask(self, text, reply_to=1):
        r = self.d.handle(111, text, chat_id=111, reply_to=reply_to)
        self.assertEqual(r.type, disp.ACCEPTED, r.message)
        return r.data["task_id"]

    def test_result_returns_to_same_chat_as_reply(self):
        tid = self.ask("альфа: переверни привет", reply_to=42)
        self.assertTrue(self.w.run_until_idle(30))
        task = self.d.store.get_task(tid)
        self.assertEqual(task["status"], store_db.DONE)
        self.assertEqual(task["result"], "тевирп")
        chat_id, reply_to, text, _ = self.chat.messages[-1]
        self.assertEqual((chat_id, reply_to), (111, 42))
        self.assertIn("✅", text)
        self.assertIn("тевирп", text)
        # Результат сохранён и в истории самого домика.
        home = self.d.registry.get("alpha").home_dir
        self.assertTrue(os.path.exists(os.path.join(home, "results", f"{tid}.md")))

    def test_failure_status_and_reason_in_chat(self):
        tid = self.ask("альфа: сломайся")
        self.w.run_until_idle(30)
        self.assertEqual(self.d.store.get_task(tid)["status"], store_db.FAILED)
        self.assertIn("не выполнена", self.chat.texts()[-1])
        self.assertIn("Тестовая ошибка", self.chat.texts()[-1])

    def test_same_project_in_order_other_project_in_parallel(self):
        a1 = self.ask("альфа: подожди 2")
        a2 = self.ask("альфа: переверни аб")
        b1 = self.ask("бета: переверни вг")
        self.assertTrue(self.w.run_until_idle(60))
        t = {k: self.d.store.get_task(k) for k in (a1, a2, b1)}
        self.assertTrue(all(x["status"] == store_db.DONE for x in t.values()))
        # Вторая задача альфы начата только после завершения первой.
        self.assertGreaterEqual(t[a2]["started_at"], t[a1]["finished_at"])
        done_order = [m[2] for m in self.chat.messages if "✅" in m[2]]
        # Бета не ждала альфу: её результат пришёл раньше, чем результат долгой задачи.
        self.assertLess(next(i for i, s in enumerate(done_order) if b1 in s),
                        next(i for i, s in enumerate(done_order) if a1 in s))

    def test_cancel_running_agent(self):
        tid = self.ask("альфа: подожди 30")
        self.w.start()
        deadline = time.monotonic() + 10
        while tid not in self.w.running() and time.monotonic() < deadline:
            time.sleep(0.05)
        r = self.d.handle(111, f"/cancel {tid}")
        self.assertIn("Останавливаю", r.message)
        deadline = time.monotonic() + 15
        while self.d.store.get_task(tid)["status"] == store_db.PROCESSING and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(self.d.store.get_task(tid)["status"], store_db.CANCELLED)
        self.assertTrue(any("остановлена" in t for t in self.chat.texts()))

    def test_status_shows_running_task(self):
        tid = self.ask("альфа: подожди 3")
        self.w.start()
        deadline = time.monotonic() + 10
        while tid not in self.w.running() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIn(f"#{tid}", self.d.handle(111, "/status").message)

    def test_confirmed_risky_task_executes_only_after_confirm(self):
        r = self.d.handle(111, "альфа: удали и переверни ок", chat_id=111, reply_to=5)
        self.assertEqual(r.type, disp.NEEDS_CONFIRMATION)
        tid = r.data["task_id"]
        self.w.run_until_idle(5)
        self.assertEqual(self.d.store.get_task(tid)["status"], store_db.AWAITING_CONFIRMATION)
        self.d.handle(111, f"/confirm {tid}")
        self.w.run_until_idle(30)
        self.assertEqual(self.d.store.get_task(tid)["status"], store_db.DONE)

    def test_long_result_split_and_available_via_result(self):
        long_text = "абв " * 3500  # 14000 символов
        tid = self.ask(f"альфа: переверни {long_text}")
        self.w.run_until_idle(30)
        mine = [m for m in self.chat.messages if m[1] == 1]
        self.assertTrue(all(len(m[2]) <= 4096 for m in mine))
        self.assertTrue(any(f"/result {tid}" in m[2] for m in mine))
        page = self.d.handle(111, f"/result {tid} 4")
        self.assertIn("страница 4 из 4", page.message)

    def test_manual_adapter_is_not_reported_as_executed(self):
        config, _ = _fixtures.build_center(allowed_user_ids=(111,))  # manual-домики
        d = Dispatcher(config)
        chat = Chat()
        w = Worker(d, notifier=chat)
        r = d.handle(111, "проверь комментарий в инстаграме", chat_id=111)
        w.run_until_idle(10)
        w.stop()
        self.assertEqual(d.store.get_task(r.data["task_id"])["status"], store_db.DELIVERED)
        self.assertIn("агент её не выполнял", chat.texts()[-1])
        d.store.close()

    def test_queue_survives_restart_and_result_goes_to_saved_chat(self):
        tid = self.ask("альфа: переверни рестарт", reply_to=77)
        # Центр "упал" до выполнения: исполнитель не успел взять задачу.
        self.w.stop()
        self.d.store.close()
        d2 = Dispatcher(self.config)
        chat2 = Chat()
        w2 = Worker(d2, notifier=chat2)
        self.assertEqual(d2.recover_after_restart()["resumed"], 1)
        w2.run_until_idle(30)
        w2.stop()
        self.assertEqual(d2.store.get_task(tid)["status"], store_db.DONE)
        self.assertEqual(chat2.messages[-1][:2], (111, 77))
        self.assertIn("тратсер", chat2.messages[-1][2])
        d2.store.close()
        self.d = Dispatcher(self.config)  # для tearDown
        self.w = Worker(self.d)


if __name__ == "__main__":
    unittest.main()
