"""Тесты реального выполнения агентов: процесс, изоляция, секреты, Claude Code."""

import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import runner  # noqa: E402
from acc.registry import Registry  # noqa: E402
from acc.runner import ExecContext  # noqa: E402

SANDBOX = runner.sandbox_available()


def _task(text, tid="t1"):
    return {"id": tid, "text": text}


class ProcessAdapterTests(unittest.TestCase):
    def setUp(self):
        self.config, _ = _fixtures.build_agent_center([("alpha", ["альфа"]), ("beta", ["бета"])])
        self.reg = Registry(self.config.homes_dir).load()
        self.alpha = self.reg.get("alpha")
        self.beta = self.reg.get("beta")

    def run_agent(self, project, text, **spec_overrides):
        spec = dict(project.adapter, **spec_overrides)
        return runner.ProcessAdapter(spec).execute(project, _task(text), ExecContext())

    def test_agent_really_executes_and_returns_output(self):
        res = self.run_agent(self.alpha, "переверни привет")
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.executed)
        self.assertEqual(res.output, "тевирп")
        # Агент реально работал в своей папке: оставил журнал.
        self.assertTrue(os.path.exists(os.path.join(self.alpha.home_dir, "runs.log")))

    def test_agent_memory_persists_in_own_home(self):
        self.run_agent(self.alpha, "запомни купить молоко")
        res = self.run_agent(self.alpha, "заметки")
        self.assertIn("купить молоко", res.output)
        self.assertIn("Заметок пока нет", self.run_agent(self.beta, "заметки").output)

    def test_env_has_own_secrets_but_not_bot_token(self):
        with open(os.path.join(self.alpha.home_dir, "secrets", "agent.env"), "w") as fh:
            fh.write("ALPHA_KEY=1\n")
        with open(os.path.join(self.beta.home_dir, "secrets", "agent.env"), "w") as fh:
            fh.write("BETA_KEY=2\n")
        with mock.patch.dict(os.environ, {"ACC_TELEGRAM_BOT_TOKEN": "bot-secret"}):
            res = self.run_agent(self.alpha, "окружение")
        self.assertIn("ALPHA_KEY", res.output)
        self.assertNotIn("BETA_KEY", res.output)
        self.assertNotIn("ACC_TELEGRAM_BOT_TOKEN", res.output)

    def test_home_is_project_folder(self):
        res = self.run_agent(self.alpha, "", command=["{python}", "-c",
                                                      "import os;print(os.environ['HOME'])"])
        self.assertEqual(res.output, os.path.realpath(self.alpha.home_dir))

    def test_failure_reported_with_stderr(self):
        res = self.run_agent(self.alpha, "сломайся")
        self.assertFalse(res.ok)
        self.assertEqual(res.exit_code, 2)
        self.assertIn("Тестовая ошибка", res.error)

    def test_timeout_stops_agent(self):
        start = time.monotonic()
        res = self.run_agent(self.alpha, "подожди 30", timeout_sec=1)
        self.assertLess(time.monotonic() - start, 15)
        self.assertTrue(res.timed_out)
        self.assertIn("Превышено время", res.error)

    def test_cancel_stops_agent(self):
        ctx = ExecContext()
        threading.Timer(1.0, ctx.cancel_event.set).start()
        start = time.monotonic()
        res = runner.ProcessAdapter(self.alpha.adapter).execute(
            self.alpha, _task("подожди 30"), ctx)
        self.assertLess(time.monotonic() - start, 15)
        self.assertTrue(res.cancelled)
        self.assertFalse(res.ok)

    def test_missing_command_is_not_executed(self):
        res = self.run_agent(self.alpha, "x", command=None)
        self.assertFalse(res.executed)

    def test_required_sandbox_refuses_when_unavailable(self):
        with mock.patch.dict(runner._SANDBOX_STATE, {"ok": False}):
            res = self.run_agent(self.alpha, "переверни а", sandbox="required")
        self.assertFalse(res.executed)
        self.assertIn("bubblewrap", res.error)
        self.assertFalse(os.path.exists(os.path.join(self.alpha.home_dir, "runs.log")))

    def test_preferred_sandbox_runs_with_note_when_unavailable(self):
        with mock.patch.dict(runner._SANDBOX_STATE, {"ok": False}):
            res = self.run_agent(self.alpha, "переверни аб", sandbox="preferred")
        self.assertTrue(res.ok)
        self.assertFalse(res.isolated)
        self.assertTrue(any("без песочницы" in n for n in res.notes))


@unittest.skipUnless(SANDBOX, "bubblewrap недоступен в этой системе")
class SandboxIsolationTests(unittest.TestCase):
    """Техническая изоляция: процесс агента не видит чужие домики и сеть."""

    def setUp(self):
        self.config, _ = _fixtures.build_agent_center([("alpha", ["альфа"]), ("beta", ["бета"])])
        reg = Registry(self.config.homes_dir).load()
        self.alpha, self.beta = reg.get("alpha"), reg.get("beta")
        self.secret = os.path.join(self.beta.home_dir, "secrets", "key.txt")
        with open(self.secret, "w", encoding="utf-8") as fh:
            fh.write("СЕКРЕТ-БЕТЫ")

    def run_alpha(self, text, **spec):
        spec = dict(self.alpha.adapter, sandbox="required", **spec)
        return runner.ProcessAdapter(spec).execute(self.alpha, _task(text), ExecContext())

    def test_cannot_read_sibling_home_by_absolute_path(self):
        res = self.run_alpha(f"прочитай {self.secret}")
        self.assertTrue(res.isolated)
        self.assertNotIn("СЕКРЕТ-БЕТЫ", res.output)
        self.assertIn("FileNotFoundError", res.output)

    def test_cannot_read_sibling_home_via_parent_traversal(self):
        res = self.run_alpha("прочитай ../beta/secrets/key.txt")
        self.assertNotIn("СЕКРЕТ-БЕТЫ", res.output)

    def test_can_work_in_own_home(self):
        res = self.run_alpha("запомни своё")
        self.assertTrue(res.ok, res.error)
        self.assertTrue(os.path.exists(os.path.join(self.alpha.home_dir, "notes.txt")))

    def test_network_disabled_when_not_allowed(self):
        code = ("import urllib.request\ntry:\n urllib.request.urlopen('https://example.com',"
                " timeout=3); print('сеть есть')\nexcept Exception as e: print('нет сети')")
        res = self.run_alpha("", command=["{python}", "-c", code], network=False)
        self.assertEqual(res.output, "нет сети")


class DnsInSandboxTests(unittest.TestCase):
    """На Ubuntu resolv.conf это ссылка в /run: без неё в песочнице нет DNS."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.target_dir = os.path.join(self.tmp, "run", "resolve")
        os.makedirs(self.target_dir)
        with open(os.path.join(self.target_dir, "stub-resolv.conf"), "w") as fh:
            fh.write("nameserver 127.0.0.53\n")
        self.link = os.path.join(self.tmp, "resolv.conf")
        os.symlink(os.path.join(self.target_dir, "stub-resolv.conf"), self.link)

    def test_symlinked_resolv_conf_folder_is_mounted(self):
        self.assertEqual(runner._dns_binds(self.link),
                         ["--ro-bind", self.target_dir, self.target_dir])

    def test_regular_file_needs_nothing(self):
        plain = os.path.join(self.tmp, "plain.conf")
        open(plain, "w").close()
        self.assertEqual(runner._dns_binds(plain), [])

    def test_only_with_network(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        with mock.patch.object(runner, "RESOLV_CONF", self.link), \
                mock.patch("shutil.which", return_value="/usr/bin/bwrap"):
            with_net = runner.build_sandbox_command(["true"], home, network=True)
            without = runner.build_sandbox_command(["true"], home, network=False)
        self.assertIn(self.target_dir, with_net)
        self.assertNotIn(self.target_dir, without)


FAKE_CLAUDE = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
with open(os.path.join(os.getcwd(), "claude_calls.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"args": args, "home": os.environ.get("HOME"),
                        "key": os.environ.get("ANTHROPIC_API_KEY"),
                        "autoupdate": os.environ.get("DISABLE_AUTOUPDATER")}},
                       ensure_ascii=False) + "\n")
resumed = "--resume" in args
err = "ошибка" in prompt
print(json.dumps({{"type": "result", "subtype": "error_during_execution" if err else "success",
                  "is_error": err, "result": "Claude сделал: " + prompt.strip(),
                  "session_id": "sess-2" if resumed else "sess-1", "total_cost_usd": 0.0012}},
                 ensure_ascii=False))
'''


class ClaudeCodeAdapterTests(unittest.TestCase):
    """Claude Code проверяется на поддельной программе claude с тем же форматом ответа."""

    def setUp(self):
        self.config, _ = _fixtures.build_center(manifests=[])
        bindir = os.path.join(tempfile.mkdtemp(), "bin")
        os.makedirs(bindir)
        self.claude = os.path.join(bindir, "claude")
        with open(self.claude, "w", encoding="utf-8") as fh:
            fh.write(FAKE_CLAUDE.format(python=sys.executable))
        os.chmod(self.claude, os.stat(self.claude).st_mode | stat.S_IEXEC)
        home = os.path.join(self.config.homes_dir, "cc")
        os.makedirs(os.path.join(home, "secrets"))
        with open(os.path.join(home, "secrets", "agent.env"), "w") as fh:
            fh.write("ANTHROPIC_API_KEY=sk-test-cc\n")
        with open(os.path.join(home, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "cc", "title": "Claude-проект", "status": "connected",
                       "keywords": ["код"],
                       "adapter": {"type": "claude_code", "claude_bin": self.claude,
                                   "sandbox": "preferred", "max_turns": 5}}, fh)
        self.project = Registry(self.config.homes_dir).load().get("cc")
        self.calls = os.path.join(home, "claude_calls.jsonl")

    def execute(self, text, session=None):
        ctx = ExecContext(session_id=session)
        return runner.ClaudeCodeAdapter(self.project.adapter).execute(self.project, _task(text), ctx)

    def test_parses_json_and_returns_session(self):
        res = self.execute("почини тест")
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.output, "Claude сделал: почини тест")
        self.assertEqual(res.session_id, "sess-1")
        self.assertTrue(any("стоимость" in n for n in res.notes))

    def test_second_run_resumes_project_session(self):
        self.execute("первое")
        res = self.execute("второе", session="sess-1")
        calls = [json.loads(l) for l in open(self.calls, encoding="utf-8")]
        self.assertNotIn("--resume", calls[0]["args"])
        self.assertIn("--resume", calls[1]["args"])
        self.assertEqual(calls[1]["args"][calls[1]["args"].index("--resume") + 1], "sess-1")
        self.assertEqual(res.session_id, "sess-2")

    def test_own_home_own_key_and_rules(self):
        self.execute("x")
        call = json.loads(open(self.calls, encoding="utf-8").readline())
        self.assertEqual(call["home"], os.path.realpath(self.project.home_dir))
        self.assertEqual(call["key"], "sk-test-cc")
        args = call["args"]
        self.assertIn("-p", args)
        self.assertEqual(args[args.index("--output-format") + 1], "json")
        rules = args[args.index("--append-system-prompt") + 1]
        self.assertIn("Не публикуй", rules)
        self.assertIn("Интернет доступен", rules)
        self.assertEqual(args[args.index("--max-turns") + 1], "5")
        self.assertNotIn("--max-budget-usd", args)
        # Самообновление выключено: папка установки в песочнице только для чтения.
        self.assertEqual(call["autoupdate"], "1")

    def test_budget_limit_and_network_off_rules(self):
        spec = dict(self.project.adapter, max_budget_usd=0.5, network=False)
        runner.ClaudeCodeAdapter(spec).execute(self.project, _task("x"), ExecContext())
        args = json.loads(open(self.calls, encoding="utf-8").readline())["args"]
        self.assertEqual(args[args.index("--max-budget-usd") + 1], "0.5")
        self.assertIn("Интернет отключён", args[args.index("--append-system-prompt") + 1])

    def _args(self, **spec_extra):
        spec = dict(self.project.adapter, **spec_extra)
        runner.ClaudeCodeAdapter(spec).execute(self.project, _task("x"), ExecContext())
        lines = open(self.calls, encoding="utf-8").read().splitlines()
        return json.loads(lines[-1])["args"]

    def test_tools_allowed_only_inside_sandbox(self):
        # В песочнице агенту разрешены команды и интернет, иначе он умеет только править файлы.
        with mock.patch.object(runner, "will_isolate", return_value=True):
            args = self._args()
        tools = args[args.index("--allowedTools") + 1].split(",")
        self.assertIn("Bash", tools)
        self.assertIn("WebFetch", tools)
        # Без песочницы ничего сверх режима acceptEdits не разрешается.
        args = self._args(sandbox="off")
        self.assertNotIn("--allowedTools", args)

    def test_explicit_tools_from_manifest_win(self):
        with mock.patch.object(runner, "will_isolate", return_value=True):
            args = self._args(allowed_tools=["Read"])
        self.assertEqual(args[args.index("--allowedTools") + 1], "Read")

    def test_claude_found_in_user_local_bin(self):
        with tempfile.TemporaryDirectory() as fake_home:
            local = os.path.join(fake_home, ".local", "bin")
            os.makedirs(local)
            target = os.path.join(local, "claude")
            with open(target, "w") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(target, 0o755)
            with mock.patch("shutil.which", return_value=None), \
                    mock.patch.dict(os.environ, {"HOME": fake_home}):
                self.assertEqual(runner.find_claude(), target)

    def test_error_result_is_failure(self):
        res = self.execute("вызови ошибка")
        self.assertFalse(res.ok)
        self.assertIn("Claude сделал", res.error)

    def test_missing_claude_is_not_executed(self):
        spec = dict(self.project.adapter, claude_bin=None)
        with mock.patch.object(runner, "find_claude", return_value=None):
            res = runner.ClaudeCodeAdapter(spec).execute(self.project, _task("x"), ExecContext())
        self.assertFalse(res.executed)
        self.assertIn("не найден", res.error)


if __name__ == "__main__":
    unittest.main()
