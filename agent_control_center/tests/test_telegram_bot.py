"""Тесты транспорта Telegram без сети и без настоящего токена (сеть подменяется)."""

import io
import json
import os
import signal
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import telegram_bot  # noqa: E402
from acc.state import State  # noqa: E402


def _make_config_file():
    config, base = _fixtures.build_center(allowed_user_ids=(111,))
    os.makedirs(os.path.join(base, "config"), exist_ok=True)
    path = os.path.join(base, "config", "control_center.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"homes_dir": config.homes_dir, "var_dir": config.var_dir,
                   "allowed_user_ids": [111]}, fh)
    return path, config


class TelegramBotTests(unittest.TestCase):
    def setUp(self):
        self._sigterm = signal.getsignal(signal.SIGTERM)
        self.cfg_path, self.config = _make_config_file()

    def tearDown(self):
        signal.signal(signal.SIGTERM, self._sigterm)

    def _run(self, env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(out), redirect_stderr(err):
            code = telegram_bot.run(self.cfg_path)
        return code, out.getvalue(), err.getvalue()

    def test_no_token_exits_with_message(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACC_TELEGRAM_BOT_TOKEN", None)
            err = io.StringIO()
            with redirect_stderr(err):
                code = telegram_bot.run(self.cfg_path)
        self.assertEqual(code, 2)
        self.assertIn("токен", err.getvalue())

    def test_bad_token_detected_at_start(self):
        def fake_call(token, method, params, timeout=60):
            raise urllib.error.HTTPError("u", 401, "Unauthorized", None, None)

        with mock.patch.object(telegram_bot, "_call", side_effect=fake_call):
            code, _, err = self._run({"ACC_TELEGRAM_BOT_TOKEN": "bad"})
        self.assertEqual(code, 3)
        self.assertIn("неверный", err)

    def test_poll_cycle_answers_owner_ignores_stranger_and_saves_offset(self):
        sent = []
        updates = [
            {"ok": True, "result": [
                {"update_id": 10, "message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "/help"}},
                {"update_id": 11, "message": {"chat": {"id": 999}, "from": {"id": 999}, "text": "привет"}},
            ]},
        ]

        def fake_call(token, method, params, timeout=60):
            if method == "getMe":
                return {"ok": True, "result": {"username": "testbot"}}
            if method == "sendMessage":
                sent.append(params)
                return {"ok": True}
            if method == "getUpdates":
                if updates:
                    return updates.pop(0)
                raise KeyboardInterrupt  # имитация остановки после одного цикла
            raise AssertionError(method)

        with mock.patch.object(telegram_bot, "_call", side_effect=fake_call):
            code, out, _ = self._run({"ACC_TELEGRAM_BOT_TOKEN": "x"})

        self.assertEqual(code, 0)
        self.assertIn("остановлен", out)
        # Ответ получил только владелец, чужому ничего не отправлено.
        self.assertEqual([p["chat_id"] for p in sent], [111])
        # Прогресс сохранён: после перезапуска бот продолжит с update_id 12.
        self.assertEqual(State(self.config.state_path).telegram_offset, 12)

    def test_long_message_is_truncated(self):
        captured = {}

        def fake_call(token, method, params, timeout=60):
            captured.update(params)
            return {"ok": True}

        with mock.patch.object(telegram_bot, "_call", side_effect=fake_call):
            telegram_bot.send_message("x", 1, "а" * 10000)
        self.assertLessEqual(len(captured["text"]), telegram_bot.TELEGRAM_TEXT_LIMIT)


if __name__ == "__main__":
    unittest.main()
