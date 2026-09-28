"""Пробный запуск (acc.cli trial): отчёт, изоляция, Telegram, отсутствие секретов.

Здесь Claude Code и Telegram подменены (формат ответов настоящий), чтобы проверить
саму проверку. Настоящий Claude Code и настоящий бот проверяются командой trial на
сервере.
"""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import cli, runner, trial  # noqa: E402
from acc import telegram_bot as tg  # noqa: E402

FAKE_CLAUDE = r'''#!{python}
import json, sys
if "--version" in sys.argv:
    print("9.9.9 (Claude Code, поддельный)")
    sys.exit(0)
prompt = sys.stdin.read()
if "Создай" in prompt and "proba.txt" in prompt:
    with open("proba.txt", "w", encoding="utf-8") as fh:
        fh.write("Клоп работает на сервере")
    answer = "Создал proba.txt"
elif "прошлой задаче" in prompt:
    answer = "proba.txt"
elif "example.com" in prompt:
    answer = "Example Domain"
else:
    answer = "Зерно, Пена, Утро"
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                  "result": answer, "session_id": "s1", "total_cost_usd": 0.01}},
                 ensure_ascii=False))
'''
KEY = "sk-ant-api03-SECRETSECRETSECRET"
TOKEN = "123456789:AASECRETSECRETSECRETSECRETSECRET123"
REPO_MANIFEST = os.path.join(_fixtures.PKG_ROOT, "homes", "trial-claude", "manifest.json")


@unittest.skipUnless(runner.sandbox_available(), "нужен bubblewrap")
class TrialTests(unittest.TestCase):
    def setUp(self):
        self.config, self.base = _fixtures.build_center(manifests=[])
        self.claude = os.path.join(tempfile.mkdtemp(), "claude")
        with open(self.claude, "w", encoding="utf-8") as fh:
            fh.write(FAKE_CLAUDE.format(python=sys.executable))
        os.chmod(self.claude, os.stat(self.claude).st_mode | stat.S_IEXEC)
        self.home = os.path.join(self.config.homes_dir, "trial-claude")
        os.makedirs(os.path.join(self.home, "secrets"))
        with open(os.path.join(self.home, "secrets", "agent.env"), "w") as fh:
            fh.write(f"ANTHROPIC_API_KEY={KEY}\n")
        with open(REPO_MANIFEST, encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest["adapter"]["claude_bin"] = self.claude
        with open(os.path.join(self.home, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False)
        os.makedirs(os.path.join(self.base, "config"))
        with open(os.path.join(self.base, "config", "telegram_token.txt"), "w") as fh:
            fh.write(TOKEN)

    def run_trial(self, telegram=True, webhook=""):
        sent, methods = [], []
        given = []

        def fake_call(token, method, params, timeout=60):
            methods.append(method)
            if method == "getMe":
                return {"ok": True, "result": {"username": "klop_trial_bot"}}
            if method == "getWebhookInfo":
                return {"ok": True, "result": {"url": webhook}}
            if method == "sendMessage":
                sent.append(params)
                return {"ok": True}
            if method == "getUpdates":
                if not given:
                    given.append(1)
                    return {"ok": True, "result": [
                        {"update_id": 5, "message": {
                            "message_id": 77, "chat": {"id": 111}, "from": {"id": 111},
                            "text": "проба: придумай три названия для кофейни"}},
                        {"update_id": 6, "message": {
                            "message_id": 78, "chat": {"id": 999}, "from": {"id": 999},
                            "text": "проба: чужой"}}]}
                return {"ok": True, "result": []}
            raise AssertionError(method)

        out = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "ACC_TELEGRAM_BOT_TOKEN"}
        with mock.patch.object(tg, "_call", side_effect=fake_call), \
                mock.patch.object(trial, "_reach", return_value=(trial.OK, "доступен")), \
                mock.patch.object(runner, "find_claude", return_value=self.claude), \
                mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out):
            code = trial.run_trial(self.config, telegram=telegram, wait=30, run_tests=False)
        path = os.path.join(self.config.var_dir, "trial_report.md")
        with open(path, encoding="utf-8") as fh:
            report = fh.read()
        return code, report, out.getvalue(), sent, methods, path

    @staticmethod
    def reply_to(params):
        rp = params.get("reply_parameters")
        return json.loads(rp)["message_id"] if rp else None

    def test_full_trial_passes_and_report_has_no_secrets(self):
        code, report, out, sent, _, path = self.run_trial()
        self.assertEqual(code, 0, report)
        for part in ("## Сервер", "## Изоляция", "## Настоящий Claude Code",
                     "## Telegram", "## Ограничения", "## Итог"):
            self.assertIn(part, report)
        for secret in (KEY, TOKEN):
            self.assertNotIn(secret, report)
            self.assertNotIn(secret, out)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertNotIn("ПРОЧИТАН", report)
        self.assertIn("результат в чат доставлен", report)
        # Результат задачи из Telegram пришёл ответом на сообщение 77, чужому ничего.
        result = [p for p in sent if "✅" in p["text"] and "Зерно" in p["text"]]
        self.assertEqual([self.reply_to(p) for p in result], [77])
        self.assertFalse([p for p in sent if p["chat_id"] == 999])
        with open(os.path.join(self.home, "proba.txt"), encoding="utf-8") as fh:
            self.assertIn("Клоп работает", fh.read())

    def test_bot_of_another_service_is_not_used(self):
        code, report, _, sent, methods, _ = self.run_trial(
            webhook="https://agent.example.org/webhook/abc")
        self.assertEqual(code, 1)
        self.assertIn("отдельный бот", report)
        self.assertNotIn("getUpdates", methods)
        self.assertNotIn("deleteWebhook", methods)
        self.assertEqual(sent, [])

    def test_without_claude_key_trial_fails_honestly(self):
        os.remove(os.path.join(self.home, "secrets", "agent.env"))
        code, report, *_ = self.run_trial(telegram=False)
        self.assertEqual(code, 1)
        self.assertIn("нет ключа", report)
        self.assertIn("Не проверялся", report)


class TrialCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "config", "control_center.json")

    def test_telegram_trial_refuses_placeholder_owner(self):
        # Номер-образец из примера настроек: бот не должен писать постороннему.
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"allowed_user_ids": [cli.PLACEHOLDER_USER_ID]}, fh)
        out = io.StringIO()
        with mock.patch.object(trial, "run_trial") as run, redirect_stdout(out):
            code = cli.main(["--config", self.cfg, "trial", "--telegram"])
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_set_owner_writes_private_config(self):
        with redirect_stdout(io.StringIO()):
            cli.main(["--config", self.cfg, "set-owner", "555"])
        with open(self.cfg, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["allowed_user_ids"], [555])
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg).st_mode), 0o600)

    def test_telegram_id_lists_senders_and_marks_old_messages_read(self):
        calls = []

        def fake_call(token, method, params, timeout=60):
            calls.append((method, dict(params)))
            if method == "getMe":
                return {"ok": True, "result": {"username": "b"}}
            if method == "getWebhookInfo":
                return {"ok": True, "result": {"url": ""}}
            if method == "getUpdates" and params.get("offset") is None:
                return {"ok": True, "result": [{"update_id": 40, "message": {
                    "from": {"id": 4242, "first_name": "Я"}, "text": "привет"}}]}
            return {"ok": True, "result": []}

        with mock.patch.object(tg, "_call", side_effect=fake_call), \
                mock.patch.dict(os.environ, {"ACC_TELEGRAM_BOT_TOKEN": "t"}):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.main(["telegram-id"])
        self.assertEqual(code, 0)
        self.assertIn("4242", out.getvalue())
        offsets = [p.get("offset") for m, p in calls if m == "getUpdates"]
        self.assertEqual(offsets, [None, 41])


if __name__ == "__main__":
    unittest.main()
