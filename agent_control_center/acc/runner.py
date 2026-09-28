"""Реальное выполнение задач агентами проектов.

Адаптер запускает агента как отдельный процесс в папке его домика, передаёт текст
задачи, ждёт завершения и возвращает результат. Запись поручения в папку
выполнением не считается (см. ManualAdapter: executed=False).

Изоляция процесса (техническая):
  - песочница bubblewrap: процесс видит только системные папки (только чтение) и
    папку своего домика; соседние домики и остальной диск ему не видны;
  - сеть в песочнице отключена, если агенту она не нужна (network: false);
  - окружение собирается с нуля: агент НЕ получает токен бота и секреты других
    домиков, только своё (secrets/agent.env) и служебные переменные;
  - HOME указывает в папку домика, поэтому настройки, сессии и память агента
    (например, ~/.claude и CLAUDE.md для Claude Code) у каждого домика свои.
Политика sandbox: required (по умолчанию, без песочницы не запускать),
preferred (запустить без неё с пометкой), off.

Типы адаптеров:
  - process      любая программа-агент (тестовый агент, свой скрипт);
  - claude_code  Claude Code без интерфейса (`claude -p`, ответ в JSON,
                 продолжение сессии проекта через --resume).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

OUTPUT_LIMIT = 1_000_000     # максимум вывода агента, который сохраняем
STDERR_LIMIT = 20_000        # хвост потока ошибок
DEFAULT_TIMEOUT = 900        # секунд на задачу по умолчанию

SANDBOX_REQUIRED = "required"
SANDBOX_PREFERRED = "preferred"
SANDBOX_OFF = "off"

SYSTEM_RO = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc"]

# Правила этапа 1 для самого агента (дополняют техническую изоляцию и границы центра).
AGENT_RULES = (
    "Ты агент проекта «{title}». Работай только в текущей папке проекта. "
    "Не публикуй контент, не пиши клиентам, не проводи платежи, не меняй токены, "
    "доступы и настройки других агентов. Ответ дай кратко, по-русски: что сделано "
    "и что получилось."
)


@dataclass
class ExecContext:
    cancel_event: threading.Event = field(default_factory=threading.Event)
    session_id: Optional[str] = None      # прошлая сессия агента проекта (resume)
    reason: str = ""                       # почему остановлено (отмена, остановка центра)


@dataclass
class ExecResult:
    ok: bool
    output: str = ""
    error: str = ""
    exit_code: Optional[int] = None
    duration: float = 0.0
    session_id: Optional[str] = None
    executed: bool = True       # False: агент не запускался
    cancelled: bool = False
    timed_out: bool = False
    isolated: bool = False      # выполнялось в песочнице
    notes: List[str] = field(default_factory=list)


# -- песочница --------------------------------------------------------------
_SANDBOX_STATE: Dict[str, bool] = {}


def _system_binds() -> List[str]:
    args: List[str] = []
    for p in SYSTEM_RO:
        args += ["--ro-bind-try", p, p]
    return args


def sandbox_available() -> bool:
    """Есть ли рабочий bubblewrap (проверяется один раз)."""
    if "ok" not in _SANDBOX_STATE:
        _SANDBOX_STATE["ok"] = _probe_sandbox()
    return _SANDBOX_STATE["ok"]


def _probe_sandbox() -> bool:
    if os.name != "posix":
        return False
    exe = shutil.which("bwrap")
    true_bin = shutil.which("true")
    if not exe or not true_bin:
        return False
    try:
        r = subprocess.run(
            [exe, "--die-with-parent", "--unshare-all", *_system_binds(), "--", true_bin],
            capture_output=True, timeout=15,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def build_sandbox_command(cmd: Sequence[str], home: str, network: bool,
                          extra_ro: Sequence[str] = ()) -> List[str]:
    home = os.path.realpath(home)
    args = [shutil.which("bwrap"), "--die-with-parent", "--new-session", "--unshare-all"]
    if network:
        args.append("--share-net")
    args += _system_binds()
    # Сначала служебные файловые системы: отдельный /tmp монтируется ДО дополнительных
    # папок, иначе он закрыл бы собой те из них, что лежат внутри /tmp.
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for p in extra_ro:
        if not p:
            continue
        real = os.path.realpath(p)
        # Нельзя открывать папку, внутри которой лежит домик: так стали бы видны соседи.
        if home == real or home.startswith(real.rstrip(os.sep) + os.sep):
            continue
        if os.path.exists(real):
            args += ["--ro-bind", real, real]
    args += ["--bind", home, home, "--chdir", home, "--"]
    return args + list(cmd)


# -- окружение агента ---------------------------------------------------------
def read_env_file(path: str) -> Dict[str, str]:
    """Секреты домика: строки KEY=VALUE, # комментарии. Файл в git не попадает."""
    env: Dict[str, str] = {}
    if not os.path.isfile(path):
        return env
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                env[k] = v
    return env


def build_env(home: str, project_id: str, task_id: str, spec: Dict) -> Dict[str, str]:
    """Окружение с нуля: только необходимое, свои секреты домика, без чужих."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "HOME": home,
        "ACC_PROJECT_ID": project_id,
        "ACC_TASK_ID": task_id,
    }
    if os.name == "nt":  # без этих переменных Windows-программы не стартуют
        for k in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT"):
            if k in os.environ:
                env[k] = os.environ[k]
        env["USERPROFILE"] = home
    for name in spec.get("pass_env", []):  # явно разрешённые переменные сервера
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(read_env_file(os.path.join(home, "secrets", "agent.env")))
    env.update({k: str(v) for k, v in spec.get("env", {}).items()})
    return env


# -- запуск процесса ----------------------------------------------------------
def _terminate(proc: subprocess.Popen) -> None:
    """Мягкая остановка, затем жёсткая. SIGINT даёт Claude Code закончить ход."""
    steps = [(signal.SIGINT, 5), (signal.SIGTERM, 3)] if os.name == "posix" else []
    for sig, wait in steps:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_process(cmd: Sequence[str], cwd: str, env: Dict[str, str], stdin_text: str,
                timeout: float, ctx: ExecContext) -> ExecResult:
    start = time.monotonic()
    kwargs = dict(cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE)
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(list(cmd), **kwargs)
    except (OSError, ValueError) as exc:
        return ExecResult(ok=False, executed=False, error=f"Не удалось запустить агента: {exc}")

    data = (stdin_text or "").encode("utf-8")
    first = True
    cancelled = timed_out = False
    while True:
        try:
            out, err = proc.communicate(input=data if first else None, timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            first = False
            if ctx.cancel_event.is_set():
                cancelled = True
            elif time.monotonic() - start > timeout:
                timed_out = True
            else:
                continue
            _terminate(proc)
            out, err = proc.communicate()
            break

    out_s = out[:OUTPUT_LIMIT].decode("utf-8", "replace")
    err_s = err[-STDERR_LIMIT:].decode("utf-8", "replace")
    res = ExecResult(
        ok=(proc.returncode == 0 and not cancelled and not timed_out),
        output=out_s.strip(),
        error=err_s.strip(),
        exit_code=proc.returncode,
        duration=time.monotonic() - start,
        cancelled=cancelled,
        timed_out=timed_out,
    )
    if len(out) > OUTPUT_LIMIT:
        res.notes.append("вывод агента обрезан до 1 МБ")
    if timed_out:
        res.error = (f"Превышено время выполнения ({int(timeout)} с), агент остановлен.\n"
                     + res.error).strip()
    if cancelled:
        res.error = (ctx.reason or "Задача отменена, агент остановлен.")
    if not res.ok and not res.error and not cancelled:
        res.error = f"Агент завершился с кодом {proc.returncode}."
    return res


def _run_in_home(project, cmd: List[str], spec: Dict, task: Dict, ctx: ExecContext,
                 extra_ro: Sequence[str] = (), default_network: bool = False) -> ExecResult:
    home = os.path.realpath(project.home_dir)
    env = build_env(home, project.id, task["id"], spec)
    policy = spec.get("sandbox", SANDBOX_REQUIRED)
    network = bool(spec.get("network", default_network))
    notes: List[str] = []
    isolated = False
    if policy != SANDBOX_OFF and sandbox_available():
        cmd = build_sandbox_command(cmd, home, network,
                                    list(extra_ro) + list(spec.get("sandbox_ro_binds", [])))
        env["TMPDIR"] = "/tmp"
        isolated = True
    elif policy == SANDBOX_REQUIRED:
        return ExecResult(
            ok=False, executed=False,
            error=("Изоляция процесса недоступна: на сервере не установлен bubblewrap "
                   "(установка: apt install bubblewrap). Агент не запущен, чтобы он не "
                   "получил доступ к чужим папкам."),
        )
    else:
        tmp = os.path.join(home, "tmp")
        os.makedirs(tmp, exist_ok=True)
        env["TMPDIR"] = tmp
        if policy != SANDBOX_OFF:
            notes.append("выполнено без песочницы: bubblewrap недоступен")
    timeout = float(spec.get("timeout_sec", DEFAULT_TIMEOUT))
    res = run_process(cmd, home, env, task["text"], timeout, ctx)
    res.isolated = isolated
    res.notes = notes + res.notes
    return res


def _expand(cmd: Sequence[str], home: str) -> List[str]:
    return [c.replace("{python}", sys.executable).replace("{home}", home) for c in cmd]


class ProcessAdapter:
    """Любая программа-агент: команда из манифеста, запуск в папке домика."""

    kind = "process"

    def __init__(self, spec: Dict) -> None:
        self.spec = spec

    def execute(self, project, task: Dict, ctx: ExecContext) -> ExecResult:
        cmd = self.spec.get("command")
        if not cmd or not isinstance(cmd, list):
            return ExecResult(ok=False, executed=False,
                              error="В манифесте не задана команда агента (adapter.command).")
        home = os.path.realpath(project.home_dir)
        extra = []
        if "{python}" in " ".join(cmd):
            extra.append(sys.base_prefix)  # интерпретатор может лежать вне /usr
        return _run_in_home(project, _expand(cmd, home), self.spec, task, ctx, extra)


class ClaudeCodeAdapter:
    """Claude Code без интерфейса. У каждого домика своя сессия, память и ключ."""

    kind = "claude_code"

    def __init__(self, spec: Dict) -> None:
        self.spec = spec

    def build_command(self, project, ctx: ExecContext, claude: str) -> List[str]:
        s = self.spec
        cmd = [claude, "-p", "--output-format", "json",
               "--permission-mode", s.get("permission_mode", "acceptEdits")]
        if s.get("unattended", True):
            cmd += ["--permission-prompts", "none"]
        if s.get("bare"):
            cmd.append("--bare")
        if s.get("model"):
            cmd += ["--model", str(s["model"])]
        if s.get("max_turns"):
            cmd += ["--max-turns", str(int(s["max_turns"]))]
        if s.get("allowed_tools"):
            cmd += ["--allowedTools", ",".join(s["allowed_tools"])]
        if s.get("disallowed_tools"):
            cmd += ["--disallowedTools", ",".join(s["disallowed_tools"])]
        rules = AGENT_RULES.format(title=project.title)
        if s.get("append_system_prompt"):
            rules += "\n" + str(s["append_system_prompt"])
        cmd += ["--append-system-prompt", rules]
        if ctx.session_id and s.get("continue_session", True):
            cmd += ["--resume", ctx.session_id]
        return cmd

    def execute(self, project, task: Dict, ctx: ExecContext) -> ExecResult:
        claude = self.spec.get("claude_bin") or shutil.which("claude")
        if not claude:
            return ExecResult(ok=False, executed=False,
                              error="Claude Code не найден на сервере (команда claude). "
                                    "Установка: npm install -g @anthropic-ai/claude-code")
        claude = os.path.abspath(claude)
        # Песочнице нужно видеть сам Claude Code и его среду выполнения (node).
        install_prefix = os.path.dirname(os.path.dirname(claude))
        extra = [install_prefix, os.path.dirname(os.path.realpath(claude))]
        cmd = self.build_command(project, ctx, claude)
        res = _run_in_home(project, cmd, self.spec, task, ctx, extra, default_network=True)
        if not res.executed or res.cancelled or res.timed_out:
            return res
        try:
            data = json.loads(res.output)
        except (json.JSONDecodeError, ValueError):
            if res.ok:
                res.notes.append("ответ Claude Code не в формате JSON, показан как есть")
            return res
        if not isinstance(data, dict):
            return res
        res.session_id = data.get("session_id") or None
        text = data.get("result")
        is_error = bool(data.get("is_error")) or data.get("subtype") not in (None, "success")
        res.output = (text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)).strip()
        if is_error:
            res.ok = False
            res.error = res.output or f"Claude Code вернул ошибку ({data.get('subtype')})."
        cost = data.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            res.notes.append(f"стоимость по оценке Claude Code: ${cost:.4f}")
        return res
