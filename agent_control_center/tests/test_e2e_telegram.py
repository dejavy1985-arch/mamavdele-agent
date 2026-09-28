"""Сквозной путь: сообщение в Telegram -> агент реально выполняет -> ответ в тот же чат.

Подменяется только сеть Telegram (токена в тестах нет). Всё остальное настоящее:
диспетчер, очередь SQLite, исполнитель, процесс тестового агента в папке домика.
"""

import io
import json
import os
import signal
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import store_db, telegram_bot  # noqa: E402
from acc.store_db import Store  # noqa: E402


def _update(uid, chat_id, message_id, text):
    return {"update_id": message_id,
            "message": {"message_id": message_id, "chat": {"id": chat_id},
                        "from": {"id": uid}, "text": text}}


class TelegramEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._sigterm = signal.getsignal(signal.SIGTERM)
        config, base = _fixtures.build_agent_center([("test-agent", ["тест"])])
        self.config = config
        os.makedirs(os.path.join(base, "config"), exist_ok=True)
        self.cfg_path = os.path.join(base, "config", "control_center.json")
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"homes_dir": config.homes_dir, "var_dir": config.var_dir,
                       "allowed_user_ids": [111]}, fh)

    def tearDown(self):
        signal.signal(signal.SIGTERM, self._sigterm)

    def run_bot(self, updates, until, timeout=20):
        """Запустить бота с поддельным Telegram; остановить, когда until(sent) истинно."""
        sent = []
        pending = [list(updates)]
        deadline = time.monotonic() + timeout

        def fake_call(token, method, params, timeout=60):
            if method == "getMe":
                return {"ok": True, "result": {"username": "klop_test_bot"}}
            if method == "sendMessage":
                sent.append(params)
                return {"ok": True}
            if method == "getUpdates":
                if pending[0]:
                    batch, pending[0] = pending[0], []
                    return {"ok": True, "result": batch}
                if until(sent) or time.monotonic() > deadline:
                    raise KeyboardInterrupt
                time.sleep(0.1)
                return {"ok": True, "result": []}
            raise AssertionError(method)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(telegram_bot, "_call", side_effect=fake_call), \
                mock.patch.dict(os.environ, {"ACC_TELEGRAM_BOT_TOKEN": "test-token"}), \
                redirect_stdout(out), redirect_stderr(err):
            code = telegram_bot.run(self.cfg_path)
        return code, sent

    @staticmethod
    def reply_to(params):
        rp = params.get("reply_parameters")
        return json.loads(rp)["message_id"] if rp else None

    def test_task_executed_and_result_returned_to_same_chat(self):
        updates = [
            _update(111, 111, 42, "тест: переверни привет"),
            _update(999, 999, 43, "тест: переверни чужое"),  # чужой: игнор
        ]
        code, sent = self.run_bot(updates, until=lambda s: any("тевирп" in p["text"] for p in s))
        self.assertEqual(code, 0)

        to_owner = [p for p in sent if p["chat_id"] == 111]
        self.assertFalse([p for p in sent if p["chat_id"] == 999])
        self.assertIn("Принято", to_owner[0]["text"])
        result = [p for p in to_owner if "тевирп" in p["text"]]
        self.assertEqual(len(result), 1)
        self.assertIn("✅", result[0]["text"])
        # И «Принято», и результат пришли ответом на исходное сообщение 42.
        self.assertEqual({self.reply_to(p) for p in to_owner}, {42})

        # Агент действительно выполнил задачу в своей папке.
        runs = os.path.join(self.config.homes_dir, "test-agent", "runs.log")
        self.assertIn("переверни привет", open(runs, encoding="utf-8").read())
        store = Store(os.path.join(self.config.var_dir, "acc.db"))
        tasks = store.list_tasks()
        store.close()
        self.assertEqual([t["status"] for t in tasks], [store_db.DONE])

    def test_status_of_failed_task_returned_to_chat(self):
        code, sent = self.run_bot([_update(111, 111, 50, "тест: сломайся")],
                                  until=lambda s: any("не выполнена" in p["text"] for p in s))
        failed = [p for p in sent if "не выполнена" in p["text"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(self.reply_to(failed[0]), 50)

    def test_interrupted_task_reported_after_restart(self):
        store = Store(os.path.join(self.config.var_dir, "acc.db"))
        t = store.enqueue_task("test-agent", "тест: подожди 100", chat_id=111, reply_to=60)
        store.claim_next()  # задача "выполнялась", когда центр упал
        store.close()
        code, sent = self.run_bot([], until=lambda s: any("прервана" in p["text"] for p in s),
                                  timeout=5)
        notice = [p for p in sent if "прервана" in p["text"]]
        self.assertEqual(len(notice), 1)
        self.assertEqual((notice[0]["chat_id"], self.reply_to(notice[0])), (111, 60))
        self.assertIn(t["id"], notice[0]["text"])


if __name__ == "__main__":
    unittest.main()
