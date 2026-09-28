"""Командная строка центра управления для работы и проверки БЕЗ Telegram.

Позволяет проверить маршрутизацию, изоляцию, очередь, историю и восстановление
на тестовых проектах, не запуская бота и не имея токена.

Запуск:
    python -m acc.cli doctor           # самопроверка конфига и домиков
    python -m acc.cli projects         # список домиков и статусов
    python -m acc.cli route "текст"    # сухой прогон: какой проект выбран
    python -m acc.cli ask "текст"      # полный путь без Telegram: агент выполняет задачу
    python -m acc.cli ask --project test-agent "переверни привет"
    python -m acc.cli send --user 111 "текст"   # то же, но с проверкой allowlist
    python -m acc.cli queue            # очередь поручений
    python -m acc.cli history          # история событий
    python -m acc.cli recover          # подхватить прерванные поручения
    python -m acc.cli add-home ID --title "..." --keywords a,b --aliases x,y
    python -m acc.cli demo             # демо на ВРЕМЕННЫХ тестовых проектах

Команда demo создаёт два ВРЕМЕННЫХ тестовых домика с настоящим тестовым агентом,
показывает весь путь (маршрут, уточнение, подтверждение, выполнение, результат,
изоляция) и удаляет их. Реальные домики не трогает.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from . import router as router_mod
from .config import ControlCenterConfig, load_config
from .dispatcher import ACCEPTED, Dispatcher
from .registry import Registry
from .worker import Worker

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(PROJECT_ROOT, "config", "control_center.json")
EXAMPLE_CONFIG = os.path.join(PROJECT_ROOT, "config", "control_center.example.json")
HOMES_DIR = os.path.join(PROJECT_ROOT, "homes")


def _resolve_config(path: str | None) -> str:
    if path:
        return path
    if os.path.exists(DEFAULT_CONFIG):
        return DEFAULT_CONFIG
    return EXAMPLE_CONFIG  # для офлайн-проверки без личного конфига


def _print(msg: str) -> None:
    print(msg)


# -- команды ---------------------------------------------------------------
def cmd_doctor(args) -> int:
    cfg_path = _resolve_config(args.config)
    _print(f"Конфиг: {cfg_path}" + (" (пример)" if cfg_path == EXAMPLE_CONFIG else ""))
    try:
        config = load_config(cfg_path)
    except Exception as exc:
        _print(f"ОШИБКА конфига: {exc}")
        return 1
    try:
        registry = Registry(config.homes_dir).load()
    except Exception as exc:
        _print(f"ОШИБКА загрузки домиков (в т.ч. проверка изоляции): {exc}")
        return 1

    _print(f"Домиков загружено: {len(registry.all())}")
    for p in registry.all():
        _print(f"  - {p.id}: статус {p.status}, адаптер {p.adapter.get('type')}")
    _print(f"allowlist user_id: {config.allowed_user_ids or '(пусто, никого не пускает)'}")
    writable = os.access(config.var_dir, os.W_OK)
    _print(f"Рабочая папка var: {config.var_dir} (запись: {'ок' if writable else 'нет'})")
    _print("Изоляция домиков: проверка при загрузке пройдена (домики не вложены).")
    _print("Итог: конфиг и домики валидны.")
    return 0


def cmd_projects(args) -> int:
    config = load_config(_resolve_config(args.config))
    registry = Registry(config.homes_dir).load()
    for p in registry.all():
        mark = "подключён" if p.is_connected() else p.status
        _print(f"{p.id} [{mark}] {p.title}")
    if not registry.all():
        _print("(домиков нет)")
    return 0


def cmd_route(args) -> int:
    config = load_config(_resolve_config(args.config))
    registry = Registry(config.homes_dir).load()
    result = router_mod.route(args.text, registry.all())
    _print(f"Исход маршрутизации: {result.outcome}")
    for c in result.candidates:
        _print(f"  кандидат {c.project_id} (счёт {c.score}, совпадения: {', '.join(c.hits) or '-'})")
    if result.outcome == router_mod.MATCH and result.best:
        proj = registry.get(result.best.project_id)
        if proj and not proj.is_connected():
            _print(f"Примечание: проект есть, но не подключён (статус {proj.status}).")
    return 0


def _run_and_print(config, user, text, project_id=None, trusted=False, timeout=300) -> int:
    """Полный путь без Telegram: всё, что пришло бы в чат, печатается сюда."""
    d = Dispatcher(config)
    w = Worker(d, notifier=lambda chat_id, t, reply_to=None: _print(t + "\n"),
               max_parallel=config.max_parallel)
    try:
        r = d.handle(user, text, trusted=trusted, project_id=project_id)
        if r.silent:
            _print("(сообщение отклонено: отправителя нет в allowlist)")
            return 1
        _print(r.message + "\n")
        if r.type != ACCEPTED:
            return 0 if r.type in ("command", "needs_confirmation", "clarify") else 1
        if not w.run_until_idle(timeout):
            _print("Задача не закончилась за отведённое время.")
            return 1
        task = d.store.get_task(r.data["task_id"])
        return 0 if task and task["status"] == "done" else 1
    finally:
        w.stop()
        d.store.close()


def cmd_ask(args) -> int:
    config = load_config(_resolve_config(args.config))
    return _run_and_print(config, "cli", args.text, project_id=args.project, trusted=True,
                          timeout=args.timeout)


def cmd_send(args) -> int:
    config = load_config(_resolve_config(args.config))
    return _run_and_print(config, args.user, args.text, timeout=args.timeout)


def cmd_queue(args) -> int:
    config = load_config(_resolve_config(args.config))
    dispatcher = Dispatcher(config)
    tasks = dispatcher.store.list_tasks(limit=50)
    if not tasks:
        _print("Очередь и задачи пусты.")
        return 0
    for t in tasks:
        _print(f"{t['id']} [{t['status']}] {t['project_id']}: {t['text'][:60]}")
    return 0


def cmd_history(args) -> int:
    config = load_config(_resolve_config(args.config))
    dispatcher = Dispatcher(config)
    for e in dispatcher.store.history(30):
        _print(f"{e.get('ts','')} {e.get('event','')} {e.get('project_id') or ''}".rstrip())
    return 0


def cmd_recover(args) -> int:
    config = load_config(_resolve_config(args.config))
    d = Dispatcher(config)
    counts = d.recover_after_restart()
    _print("Восстановление: прервано перезапуском {interrupted}, ждут выполнения "
           "{resumed}, ждут подтверждения {awaiting}.".format(**counts))
    if counts["resumed"]:
        w = Worker(d, notifier=lambda c, t, r=None: _print(t + "\n"),
                   max_parallel=config.max_parallel)
        w.run_until_idle(600)
        w.stop()
    d.store.close()
    return 0


def cmd_add_home(args) -> int:
    home = os.path.join(HOMES_DIR, args.id)
    manifest_path = os.path.join(home, "manifest.json")
    if os.path.exists(manifest_path):
        _print(f"Домик '{args.id}' уже существует: {manifest_path}")
        return 1
    keywords = [k.strip() for k in (args.keywords or "").split(",") if k.strip()]
    aliases = [a.strip() for a in (args.aliases or "").split(",") if a.strip()]
    manifest = {
        "id": args.id,
        "title": args.title or args.id,
        "summary": args.summary or "",
        # Новый домик всегда registered: подключение это отдельный осознанный шаг.
        "status": "registered",
        "keywords": keywords,
        "aliases": aliases,
        "adapter": {"type": "manual", "notes": "manual: поручение пишется в inbox домика."},
        "allowed_actions": ["status", "route_task", "read_memory", "append_memory"],
        "boundaries_note": "Центр не публикует, не пишет клиентам, не платит, не меняет токены.",
    }
    os.makedirs(os.path.join(home, "memory"), exist_ok=True)
    os.makedirs(os.path.join(home, "secrets"), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(home, "secrets", ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("*\n!.gitignore\n")
    open(os.path.join(home, "memory", ".gitkeep"), "a").close()
    _print(f"Создан домик '{args.id}' со статусом registered: {home}")
    _print("Ядро менять не нужно. Когда решите подключить, смените статус на connected.")
    return 0


def cmd_demo(args) -> int:
    """Полный прогон на ВРЕМЕННЫХ тестовых домиках с настоящим тестовым агентом."""
    import shutil
    from .runner import sandbox_available

    agent_src = os.path.join(HOMES_DIR, "test-agent", "agent.py")
    base = tempfile.mkdtemp(prefix="acc-demo-")
    homes = os.path.join(base, "homes")
    var = os.path.join(base, "var")
    os.makedirs(var)

    def make(pid, title, keywords):
        home = os.path.join(homes, pid)
        os.makedirs(os.path.join(home, "secrets"))
        shutil.copy(agent_src, os.path.join(home, "agent.py"))
        with open(os.path.join(home, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": pid, "title": title, "status": "connected", "test": True,
                       "keywords": keywords,
                       "adapter": {"type": "process", "command": ["{python}", "agent.py"],
                                   "sandbox": "preferred", "network": False,
                                   "timeout_sec": 60}}, fh, ensure_ascii=False)
        return home

    make("test-alpha", "Тест Альфа", ["альфа"])
    beta = make("test-beta", "Тест Бета", ["бета"])
    secret = os.path.join(beta, "secrets", "key.txt")
    with open(secret, "w", encoding="utf-8") as fh:
        fh.write("СЕКРЕТ-БЕТЫ")

    config = ControlCenterConfig(base_dir=base, homes_dir=homes, var_dir=var,
                                 allowed_user_ids=[111])
    d = Dispatcher(config)
    chat = []
    w = Worker(d, notifier=lambda c, t, r=None: chat.append(t))
    scenario = [
        (999, "альфа: переверни привет"),                 # чужой: тихий отказ
        (111, "альфа: переверни привет"),                 # выполнит альфа
        (111, "бета: посчитай слова раз два три"),        # выполнит бета
        (111, "альфа и бета"),                             # уточнение
        (111, "опубликуй пост"),                           # граница этапа 1
        (111, "альфа: удали отчёт"),                       # нужно подтверждение
        # Агент АЛЬФЫ (проект выбран явно) пытается прочитать секрет беты.
        (111, f"/to test-alpha прочитай {secret}"),
    ]
    _print("ДЕМО на ВРЕМЕННЫХ тестовых домиках (test-alpha, test-beta) с настоящим тестовым агентом.")
    _print("Это не ваши реальные проекты. По завершении всё удаляется.\n")
    for uid, text in scenario:
        r = d.handle(uid, text, chat_id=uid)
        first = r.message.splitlines()[0] if r.message else ""
        _print(f"[user {uid}] {text!r}\n   -> {r.type}: {first}")
    w.run_until_idle(60)
    w.stop()
    _print("\nЧто пришло в чат от агентов:")
    for t in chat:
        _print("   " + t.replace("\n\n", " | ").replace("\n", " "))
    leaked = any("Тест Альфа" in t and "СЕКРЕТ-БЕТЫ" in t for t in chat)
    _print("\nИзоляция домиков:")
    _print(f"   песочница bubblewrap: {'включена' if sandbox_available() else 'недоступна'}")
    _print(f"   агент альфы прочитал секрет беты: {'ДА, изоляции нет' if leaked else 'нет'}")
    d.store.close()
    shutil.rmtree(base, ignore_errors=True)
    _print("\nВременные домики удалены. Реальные домики не затронуты.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="acc", description="Центр управления агентами (офлайн-инструменты)")
    p.add_argument("--config", help="путь к control_center.json (по умолчанию рабочий или пример)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="самопроверка конфига и домиков").set_defaults(func=cmd_doctor)
    sub.add_parser("projects", help="список домиков").set_defaults(func=cmd_projects)

    pr = sub.add_parser("route", help="сухой прогон маршрутизации")
    pr.add_argument("text")
    pr.set_defaults(func=cmd_route)

    pk = sub.add_parser("ask", help="полный путь без Telegram: агент выполняет задачу")
    pk.add_argument("--project", help="id проекта (иначе определяется по тексту)")
    pk.add_argument("--timeout", type=float, default=300)
    pk.add_argument("text")
    pk.set_defaults(func=cmd_ask)

    ps = sub.add_parser("send", help="как ask, но с проверкой allowlist по user_id")
    ps.add_argument("--user", type=int, required=True)
    ps.add_argument("--timeout", type=float, default=300)
    ps.add_argument("text")
    ps.set_defaults(func=cmd_send)

    sub.add_parser("queue", help="очередь поручений").set_defaults(func=cmd_queue)
    sub.add_parser("history", help="история событий").set_defaults(func=cmd_history)
    sub.add_parser("recover", help="разобрать задачи после перезапуска и выполнить очередь").set_defaults(func=cmd_recover)

    pa = sub.add_parser("add-home", help="добавить домик (без правки ядра)")
    pa.add_argument("id")
    pa.add_argument("--title")
    pa.add_argument("--summary")
    pa.add_argument("--keywords", help="через запятую")
    pa.add_argument("--aliases", help="через запятую")
    pa.set_defaults(func=cmd_add_home)

    sub.add_parser("demo", help="демо на временных тестовых проектах").set_defaults(func=cmd_demo)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
