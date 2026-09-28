"""Пробный запуск Клопа на сервере: настоящий Claude Code и настоящий Telegram.

Запуск на сервере из папки agent_control_center:
    python3 -m acc.cli trial              # сервер, песочница, изоляция, Claude Code
    python3 -m acc.cli trial --telegram   # плюс Telegram: ждёт задачу из вашего чата

Проверка работает только с ТЕСТОВЫМ домиком trial-claude. Рабочих агентов не трогает
и не запускает. Отчёт печатается и сохраняется в var/trial_report.md в трёх частях:
что выполнилось на сервере, что пришло в Telegram, какие ограничения остались.
Секретов в отчёте нет: про ключи и токены пишется только, есть они или нет.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from . import runner
from . import store_db
from . import telegram_bot as tg
from .dispatcher import ACCEPTED, Dispatcher
from .registry import Registry
from .runner import ExecContext
from .worker import Worker

TRIAL_ID = "trial-claude"
KEY_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
OK, FAIL, WARN, SKIP = "✅", "❌", "⚠️", "⏭"
TG_TASK = "проба: придумай три названия для кофейни"

LIMITS = [
    "Агент с доступом в интернет может обращаться и к сетевым службам самого сервера "
    "(localhost): сеть в песочнице общая с сервером. Поэтому на сервере Клопа не стоит "
    "держать другие службы без пароля.",
    "Ключ Claude виден самому агенту: это его ключ. Ключи других домиков и токен бота "
    "ему недоступны (проверено выше).",
    "Правила «не публиковать, не писать клиентам, не платить» агент получает текстом. "
    "Технически их держит то, что у агента нет чужих ключей и доступов. Ключи "
    "публикаций и соцсетей агентам на этом этапе не выдаются.",
    "Каждая задача Claude Code платная. Лимит пробного агента: 0,5 доллара и 10 ходов "
    "на задачу (max_budget_usd, max_turns в манифесте).",
    "Служба с автозапуском после перезагрузки сервера пробным запуском не включается "
    "и не проверяется. Её включают отдельным шагом после проверки.",
]

_SECRET_PATTERNS = [
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),   # токен Telegram-бота
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),           # ключи и токены Anthropic
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),            # прочие ключи вида sk-
]


def redact(text: str, secrets: List[str]) -> str:
    for s in secrets:
        if s and len(s) >= 8:
            text = text.replace(s, "[скрыто]")
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[скрыто]", text)
    return text


class Report:
    """Отчёт по разделам. Строки сразу печатаются, секреты вырезаются."""

    def __init__(self, secrets: List[str], echo: bool = True) -> None:
        self.secrets = [s for s in secrets if s]
        self.echo = echo
        self.header: List[str] = []
        self.sections: List[Tuple[str, List[Tuple[str, str, str]]]] = []

    def _out(self, text: str) -> None:
        if self.echo:
            print(redact(text, self.secrets), flush=True)

    def section(self, title: str) -> None:
        self.sections.append((title, []))
        self._out(f"\n## {title}")

    def add(self, mark: str, name: str, detail: str = "") -> None:
        self.sections[-1][1].append((mark, name, detail))
        self._out(_line(mark, name, detail))

    @property
    def failed(self) -> bool:
        return any(m == FAIL for _, items in self.sections for m, _, _ in items)

    def problems(self) -> List[str]:
        return [f"{m} {title}: {name}" for title, items in self.sections
                for m, name, _ in items if m in (FAIL, WARN)]

    def markdown(self) -> str:
        out = list(self.header)
        for title, items in self.sections:
            out += ["", f"## {title}", ""]
            out += [_line(m, n, d) for m, n, d in items]
        return redact("\n".join(out) + "\n", self.secrets)


def _line(mark: str, name: str, detail: str) -> str:
    if not detail:
        return f"- {mark} {name}" if mark else f"- {name}"
    detail = detail.strip()
    if "\n" in detail:
        body = "\n".join("    " + l for l in detail.splitlines())
        return f"- {mark} {name}:\n{body}"
    return f"- {mark} {name}: {detail}"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _short(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


# -- сервер -------------------------------------------------------------------
def _os_name() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def _meminfo() -> Optional[Tuple[float, float]]:
    try:
        data = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                k, v = line.split(":", 1)
                data[k] = int(v.split()[0]) / 1024 / 1024
        return data["MemTotal"], data.get("MemAvailable", 0.0)
    except (OSError, KeyError, ValueError):
        return None


def _reach(url: str, anthropic: bool = False) -> Tuple[str, str]:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return OK, f"доступен (ответ HTTP {resp.status})"
    except urllib.error.HTTPError as exc:
        if anthropic and exc.code == 403:
            return WARN, ("HTTP 403: сервер Claude отказывает. Так бывает, если страна "
                          "сервера не поддерживается Anthropic (например, Россия)")
        return OK, f"доступен (ответ HTTP {exc.code})"
    except Exception as exc:  # нет сети, DNS, запрет
        reason = getattr(exc, "reason", exc)
        return FAIL, f"недоступен: {reason}"


def check_server(rep: Report, config) -> None:
    rep.section("Сервер")
    rep.add(OK, "Система", f"{_os_name()}, ядро {platform.release()}, "
                           f"Python {platform.python_version()}")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        rep.add(WARN, "Пользователь", "проверка запущена от root. Клопа лучше запускать "
                                      "от отдельного пользователя (install_server.sh "
                                      "создаёт пользователя klop)")
    else:
        rep.add(OK, "Пользователь", getpass.getuser())
    mem = _meminfo()
    if mem:
        total, avail = mem
        rep.add(OK if total >= 1.8 else WARN, "Память",
                f"всего {total:.1f} ГБ, свободно {avail:.1f} ГБ")
    free = shutil.disk_usage(config.base_dir).free / 2 ** 30
    rep.add(OK if free >= 5 else WARN, "Диск", f"свободно {free:.1f} ГБ")
    if runner.sandbox_available():
        rep.add(OK, "Песочница bubblewrap", "работает")
    else:
        rep.add(FAIL, "Песочница bubblewrap",
                "не работает, агенты с обязательной песочницей не запустятся. Решение: "
                "sudo bash scripts/install_server.sh (ставит bubblewrap и при "
                "необходимости разрешает его в AppArmor)")
    claude = runner.find_claude()
    if claude:
        try:
            ver = subprocess.run([claude, "--version"], capture_output=True, text=True,
                                 timeout=60).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            ver = f"не отвечает: {exc}"
        rep.add(OK if ver and "не отвечает" not in ver else FAIL, "Claude Code",
                ver or "не отвечает")
    else:
        rep.add(FAIL, "Claude Code", "не найден. Установка: "
                                     "curl -fsSL https://claude.ai/install.sh | bash")
    mark, detail = _reach("https://api.anthropic.com/v1/models", anthropic=True)
    rep.add(mark, "Связь с сервером Claude (api.anthropic.com)", detail)
    mark, detail = _reach("https://api.telegram.org/")
    rep.add(mark, "Связь с Telegram (api.telegram.org)", detail)


def check_tests(rep: Report, base_dir: str) -> None:
    script = os.path.join(base_dir, "tests", "run_tests.py")
    if not os.path.exists(script):
        rep.add(SKIP, "Автотесты", "папки tests нет")
        return
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                           timeout=900, cwd=base_dir)
    except subprocess.TimeoutExpired:
        rep.add(FAIL, "Автотесты", "не закончились за 15 минут")
        return
    out = r.stdout + r.stderr
    ran = re.findall(r"Ran (\d+) tests?", out)
    skipped = re.findall(r"skipped=(\d+)", out)
    detail = f"{ran[-1] if ran else '?'} тестов"
    if skipped:
        detail += f", пропущено {skipped[-1]}"
    if r.returncode == 0:
        rep.add(OK, "Автотесты", detail + ", все прошли")
    else:
        bad = [l for l in out.splitlines() if l.startswith(("FAIL:", "ERROR:"))][:5]
        rep.add(FAIL, "Автотесты", detail + ", есть ошибки:\n" + "\n".join(bad))


# -- изоляция -----------------------------------------------------------------
PROBE = r'''
import json, os, sys
targets = json.loads(sys.argv[1])
res = {}
for name, path in targets.items():
    try:
        with open(path, "rb") as fh:
            fh.read(1)
        res[name] = "READ"
    except Exception as exc:
        res[name] = type(exc).__name__
try:
    res["_near"] = sorted(os.listdir(".."))
except Exception as exc:
    res["_near"] = [type(exc).__name__]
res["_tg_env"] = sorted(k for k in os.environ if "TELEGRAM" in k.upper())
print(json.dumps(res, ensure_ascii=False))
'''


def check_isolation(rep: Report, config, project) -> None:
    rep.section("Изоляция пробного агента (проверка программой в той же песочнице)")
    neighbor = os.path.join(config.homes_dir, "_trial_neighbor")  # без манифеста: не домик
    canary = os.path.join(neighbor, "secrets", "key.txt")
    os.makedirs(os.path.dirname(canary), exist_ok=True)
    with open(canary, "w", encoding="utf-8") as fh:
        fh.write("проверочный секрет соседа")
    targets = {
        "Секрет соседней папки по полному пути": canary,
        "Секрет соседней папки через ..": os.path.join("..", "_trial_neighbor", "secrets",
                                                       "key.txt"),
        "База задач центра": os.path.join(config.var_dir, "acc.db"),
    }
    token_file = os.path.join(config.base_dir, "config", "telegram_token.txt")
    if os.path.exists(token_file):
        targets["Файл с токеном бота"] = token_file
    for other in Registry(config.homes_dir).load().all():
        env_file = os.path.join(other.home_dir, "secrets", "agent.env")
        if other.id != project.id and os.path.exists(env_file):
            targets[f"Ключи домика {other.id}"] = env_file
    spec = {"command": ["{python}", "-c", PROBE, json.dumps(targets)],
            "sandbox": "required", "network": bool(project.adapter.get("network", True)),
            "timeout_sec": 60}
    try:
        res = runner.ProcessAdapter(spec).execute(
            project, {"id": "trial-isolation", "text": ""}, ExecContext())
    finally:
        shutil.rmtree(neighbor, ignore_errors=True)
    if not res.executed or not res.ok:
        rep.add(FAIL, "Проверка не выполнилась", _short(res.error, 400))
        return
    try:
        data = json.loads(res.output)
    except ValueError:
        rep.add(FAIL, "Проверка вернула непонятный ответ", _short(res.output, 300))
        return
    for name in targets:
        got = data.get(name)
        rep.add(FAIL if got == "READ" else OK, name,
                "ПРОЧИТАН, изоляции нет" if got == "READ" else f"недоступен ({got})")
    near = data.get("_near", [])
    own = os.path.basename(os.path.realpath(project.home_dir))
    rep.add(OK if near == [own] else FAIL, "Что агент видит рядом со своей папкой",
            ", ".join(near) or "ничего")
    tg_env = data.get("_tg_env", [])
    rep.add(OK if not tg_env else FAIL, "Переменные Telegram у агента",
            "нет" if not tg_env else ", ".join(tg_env))


# -- Claude Code --------------------------------------------------------------
def key_source(project) -> str:
    env = runner.read_env_file(os.path.join(project.home_dir, "secrets", "agent.env"))
    for var, label in (("ANTHROPIC_API_KEY", "ключ API"),
                       ("CLAUDE_CODE_OAUTH_TOKEN", "токен подписки Claude")):
        if env.get(var):
            return f"{label} ({var}) в secrets/agent.env"
    passed = [n for n in project.adapter.get("pass_env", []) if os.environ.get(n)]
    if passed:
        return "переменные сервера: " + ", ".join(passed)
    return ""


def _run_task(dispatcher: Dispatcher, worker: Worker, text: str,
              timeout: float) -> Tuple[Optional[Dict], str, float]:
    r = dispatcher.handle("trial", text, trusted=True, project_id=TRIAL_ID)
    if r.type != ACCEPTED:
        return None, r.message, 0.0
    start = time.monotonic()
    worker.run_until_idle(timeout)
    return dispatcher.store.get_task(r.data["task_id"]), "", time.monotonic() - start


def check_claude(rep: Report, dispatcher: Dispatcher, worker: Worker, project,
                 timeout: float) -> None:
    rep.section("Настоящий Claude Code: задачи через очередь Клопа, без Telegram")
    source = key_source(project)
    if not source:
        rep.add(FAIL, "Доступ к Claude", "нет ключа. Запустите: bash scripts/setup_secrets.sh")
        return
    rep.add(OK, "Доступ к Claude", source)
    proba = os.path.join(project.home_dir, "proba.txt")
    if os.path.exists(proba):
        os.remove(proba)

    steps = [
        ("Задача 1: создать файл",
         "Создай в текущей папке файл proba.txt с текстом «Клоп работает на сервере» "
         "и коротко ответь, что сделал.",
         lambda t: "Клоп работает" in _read(proba), FAIL),
        ("Задача 2: продолжение разговора",
         "Какой файл ты создал в прошлой задаче? Ответь одной фразой.",
         lambda t: "proba" in (t.get("result") or "").lower(), WARN),
        ("Задача 3: интернет у агента",
         "Открой https://example.com и напиши заголовок страницы.",
         lambda t: "example domain" in (t.get("result") or "").lower(), WARN),
    ]
    for name, text, check, bad_mark in steps:
        task, refused, secs = _run_task(dispatcher, worker, text, timeout)
        if task is None:
            rep.add(FAIL, name, "Клоп не принял задачу: " + _short(refused, 300))
            continue
        status = task["status"]
        answer = _short(task.get("result") or "")
        detail = f"статус {status}, {secs:.0f} с, в песочнице\nЗадача: {text}\nОтвет: {answer}"
        if status != store_db.DONE:
            rep.add(FAIL, name, detail)
            if name.startswith("Задача 1"):
                return  # без первой задачи дальше проверять нечего
            continue
        rep.add(OK if check(task) else bad_mark, name, detail)
    if os.path.exists(proba):
        rep.add(OK, "Файл proba.txt в папке пробного агента",
                _read(proba).strip()[:200])


# -- Telegram -----------------------------------------------------------------
def check_telegram(rep: Report, config, dispatcher: Dispatcher, worker: Worker,
                   sent: List[Dict], token_box: Dict, wait: float) -> None:
    rep.section("Telegram: что пришло в чат")
    token = config.telegram_token()
    if not token:
        rep.add(FAIL, "Токен бота", "нет. Запустите: bash scripts/setup_secrets.sh")
        return
    try:
        name = tg.check_token(token)
        hook = tg.webhook_host(token)
    except ValueError as exc:
        rep.add(FAIL, "Токен бота", str(exc))
        return
    except Exception as exc:
        rep.add(FAIL, "Связь с Telegram", str(exc))
        return
    rep.add(OK, "Бот", f"@{name}")
    if hook:
        rep.add(FAIL, "Бот уже занят", f"у бота настроен webhook ({hook}): его сообщения "
                "получает другой сервис, возможно действующий агент. Для Клопа нужен "
                "отдельный бот от @BotFather. Чужой webhook не тронут.")
        return
    owners = dispatcher.allowlist.ids()
    if not owners:
        rep.add(FAIL, "Доступ", "не указан ваш user_id. Запустите: bash scripts/setup_secrets.sh")
        return
    owner = owners[0]
    rep.add(OK, "Доступ", f"бот принимает команды только от {len(owners)} user_id "
                          f"(ваш оканчивается на …{str(owner)[-3:]})")

    token_box["t"] = token
    intro = (f"🧪 Клоп на сервере: пробный запуск.\n\nНапишите мне сообщение:\n{TG_TASK}\n\n"
             f"Жду {int(wait // 60)} мин.")
    ok = tg.send_message(token, owner, intro)
    rep.add(OK if ok else FAIL, "Первое сообщение от бота вам",
            "Telegram принял" if ok else "не доставлено: откройте бота в Telegram, нажмите "
            "Start и запустите проверку снова")
    print(f"\nЖду сообщение в Telegram до {int(wait // 60)} мин. Напишите боту: {TG_TASK}",
          flush=True)

    worker.start()
    offset = dispatcher.state.telegram_offset
    deadline = time.monotonic() + wait
    trial_task: Optional[str] = None
    incoming: List[Tuple[Dict, object]] = []
    conflict = ""
    while time.monotonic() < deadline:
        if trial_task:
            t = dispatcher.store.get_task(trial_task)
            if t and t["status"] in store_db.FINAL_STATUSES and not worker.running():
                break
        try:
            updates = tg.get_updates(token, offset,
                                     timeout=int(max(1, min(20, deadline - time.monotonic()))))
        except tg.TelegramConflict as exc:
            conflict = str(exc)
            break
        except Exception:
            time.sleep(3)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            dispatcher.state.set_telegram_offset(offset)
            message, response = tg.handle_update(token, dispatcher, dispatcher.audit, update)
            incoming.append((message, response))
            if (response is not None and response.type == ACCEPTED
                    and response.project_id == TRIAL_ID and not trial_task):
                trial_task = response.data["task_id"]
    time.sleep(1)  # дать исполнителю отправить последнее сообщение

    if conflict:
        rep.add(FAIL, "Приём сообщений", conflict)
    for message, response in incoming:
        uid = str((message.get("from") or {}).get("id", ""))
        text = _short(message.get("text", ""), 200)
        if response is None:
            rep.add(FAIL, f"Ваше сообщение «{text}»", "внутренняя ошибка, см. журнал")
        elif response.silent:
            rep.add(WARN, f"Сообщение от …{uid[-3:]} «{text}»",
                    "отклонено: этого отправителя нет в списке доступа")
        else:
            first = (response.message or "").splitlines()[0] if response.message else ""
            rep.add(OK, f"Ваше сообщение «{text}»", f"бот ответил ответом на него: {first}")
    for item in sent:
        if item["chat_id"] is None:
            continue
        head = _short(item["text"], 700)
        mark = OK if item["ok"] else FAIL
        where = "ответом на ваше сообщение" if item["reply_to"] else "отдельным сообщением"
        rep.add(mark, f"Бот прислал {where}" + ("" if item["ok"] else " (НЕ доставлено)"),
                head)
    if trial_task:
        t = dispatcher.store.get_task(trial_task)
        status = t["status"] if t else "?"
        delivered = any(i["chat_id"] is not None and i["ok"] and trial_task in i["text"]
                        and ("✅" in i["text"] or "❌" in i["text"]) for i in sent)
        rep.add(OK if status == store_db.DONE and delivered else FAIL,
                "Задача из Telegram",
                f"статус {status}; результат в чат {'доставлен' if delivered else 'НЕ доставлен'}")
    elif not conflict:
        rep.add(WARN, "Задача из Telegram",
                f"за {int(wait // 60)} мин сообщение «{TG_TASK}» не пришло")


# -- запуск -------------------------------------------------------------------
def _commit(base_dir: str) -> str:
    try:
        r = subprocess.run(["git", "-C", base_dir, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def run_trial(config, telegram: bool = False, wait: float = 600, run_tests: bool = True,
              task_timeout: float = 600) -> int:
    registry = Registry(config.homes_dir).load()
    project = registry.get(TRIAL_ID)
    secrets = [config.telegram_token() or ""]
    if project is not None:
        env = runner.read_env_file(os.path.join(project.home_dir, "secrets", "agent.env"))
        secrets += list(env.values())
    rep = Report(secrets)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rep.header = [
        "# Пробный запуск Клопа на сервере",
        "",
        f"Время: {stamp}. Версия Клопа: {_commit(config.base_dir)}. "
        f"Проверяется только тестовый домик {TRIAL_ID}; рабочие агенты не запускались.",
    ]
    print("\n".join(rep.header), flush=True)

    check_server(rep, config)
    if run_tests:
        check_tests(rep, config.base_dir)

    if project is None or not project.test:
        rep.section("Пробный агент")
        rep.add(FAIL, "Домик", f"нет тестового домика homes/{TRIAL_ID}")
    else:
        check_isolation(rep, config, project)
        dispatcher = Dispatcher(config)
        sent: List[Dict] = []
        token_box: Dict = {}

        def notify(chat_id, text, reply_to=None):
            ok = None
            if chat_id is not None and token_box.get("t"):
                ok = tg.send_message(token_box["t"], chat_id, text, reply_to)
            sent.append({"chat_id": chat_id, "text": text, "reply_to": reply_to, "ok": ok})

        worker = Worker(dispatcher, notifier=notify, max_parallel=config.max_parallel)
        try:
            check_claude(rep, dispatcher, worker, project, task_timeout)
            if telegram:
                check_telegram(rep, config, dispatcher, worker, sent, token_box, wait)
            else:
                rep.section("Telegram: что пришло в чат")
                rep.add(SKIP, "Не проверялся", "запустите проверку с ключом --telegram")
        finally:
            worker.stop()
            dispatcher.store.close()

    rep.section("Ограничения, которые остаются")
    for text in LIMITS:
        rep.add("", text)
    problems = rep.problems()
    rep.section("Итог")
    if rep.failed:
        rep.add(FAIL, "Проверка не пройдена", "исправьте пункты ниже и запустите снова\n"
                + "\n".join(problems))
    elif problems:
        rep.add(WARN, "Проверка пройдена с замечаниями", "\n".join(problems))
    else:
        rep.add(OK, "Проверка пройдена", "все пункты выполнены")

    path = os.path.join(config.var_dir, "trial_report.md")
    os.makedirs(config.var_dir, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(rep.markdown())
    print(f"\nОтчёт сохранён: {path}\nВ нём нет ключей и токенов, его можно переслать.",
          flush=True)
    return 1 if rep.failed else 0
