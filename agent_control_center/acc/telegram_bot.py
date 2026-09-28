"""Транспорт Telegram (long polling), запускается ЛОКАЛЬНО на компьютере.

Использует только стандартную библиотеку (urllib), без сторонних зависимостей.
Токен берётся из переменной окружения (по умолчанию ACC_TELEGRAM_BOT_TOKEN).

Восстановление после перезапуска: смещение обработанных сообщений (update_id)
хранится в state.json. При старте бот продолжает с сохранённого смещения, поэтому
уже обработанные сообщения не дублируются, а пропущенные подхватываются.

Этот модуль сознательно тонкий: вся логика (allowlist, маршрутизация, границы,
журнал) лежит в диспетчере и покрыта офлайн-тестами. Здесь только сеть и цикл.
"""

from __future__ import annotations

import json
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import audit as audit_mod
from .config import load_config
from .dispatcher import Dispatcher

API = "https://api.telegram.org/bot{token}/{method}"


def _call(token: str, method: str, params: dict, timeout: int = 60) -> dict:
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
        return json.loads(resp.read().decode("utf-8"))


TELEGRAM_TEXT_LIMIT = 4000  # у Telegram предел 4096 символов на сообщение


def check_token(token: str) -> str:
    """Проверить токен запросом getMe. Возвращает имя бота или бросает ошибку.

    Нужна, чтобы при неверном токене сразу сказать об этом, а не повторять
    запросы бесконечно."""
    try:
        resp = _call(token, "getMe", {}, timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 404):
            raise ValueError("Telegram отклонил токен: он неверный или отозван.") from exc
        raise
    if not resp.get("ok"):
        raise ValueError(f"Telegram вернул ошибку: {resp.get('description')}")
    return resp.get("result", {}).get("username", "")


def send_message(token: str, chat_id, text: str) -> None:
    if len(text) > TELEGRAM_TEXT_LIMIT:
        text = text[: TELEGRAM_TEXT_LIMIT - 20] + "\n...(сокращено)"
    try:
        _call(token, "sendMessage", {"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as exc:  # сеть не должна ронять цикл
        print(f"[telegram] ошибка отправки: {exc}", file=sys.stderr)


def run(config_path: str) -> int:
    config = load_config(config_path)
    token = config.telegram_token()
    if not token:
        print(
            f"Не задан токен бота. Задайте переменную окружения "
            f"{config.telegram_token_env} или положите токен в config/telegram_token.txt.",
            file=sys.stderr,
        )
        return 2

    try:
        bot_name = check_token(token)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except Exception as exc:  # нет сети и т.п.: сообщаем и выходим, служба перезапустит
        print(f"Не удалось связаться с Telegram: {exc}", file=sys.stderr)
        return 4
    print(f"Токен принят, бот @{bot_name}.")

    dispatcher = Dispatcher(config)
    log = dispatcher.audit

    # Восстановление после перезапуска: подхватить прерванные поручения.
    recovered = dispatcher.recover_after_restart()
    if any(recovered.values()):
        print(f"Восстановление после перезапуска: {recovered}")

    if len(dispatcher.allowlist) == 0:
        print(
            "ВНИМАНИЕ: allowlist пуст, бот не примет ни одного сообщения. "
            "Добавьте свой Telegram user_id в config/control_center.json.",
            file=sys.stderr,
        )

    def _stop(signum, frame):  # остановка службы (SIGTERM) как Ctrl+C
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)

    log.log("bot_start", level=audit_mod.INFO)
    print("Центр управления запущен. Ожидаю сообщения в Telegram...")
    try:
        _poll_loop(token, dispatcher, log)
    except KeyboardInterrupt:
        # Штатная остановка (Ctrl+C или остановка службы): без трассировки ошибки.
        log.log("bot_stop", level=audit_mod.INFO)
        print("Центр управления остановлен.")
    finally:
        dispatcher.store.close()
    return 0


def _poll_loop(token: str, dispatcher: Dispatcher, log) -> None:
    offset = dispatcher.state.telegram_offset
    while True:
        try:
            resp = _call(
                token,
                "getUpdates",
                {"offset": offset, "timeout": 50, "allowed_updates": json.dumps(["message"])},
                timeout=50,
            )
        except Exception as exc:
            log.log("poll_error", level=audit_mod.ERROR, text=str(exc))
            time.sleep(3)  # пауза и повтор, цикл не падает
            continue

        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            dispatcher.state.set_telegram_offset(offset)  # фиксируем прогресс сразу

            message = update.get("message") or {}
            chat = message.get("chat") or {}
            user = message.get("from") or {}
            text = message.get("text", "")
            user_id = user.get("id")
            chat_id = chat.get("id")

            try:
                response = dispatcher.handle(user_id, text)
            except Exception as exc:
                log.log("handle_error", level=audit_mod.ERROR, user_id=user_id, text=str(exc))
                if chat_id is not None:
                    send_message(token, chat_id, "Внутренняя ошибка. Записал в журнал.")
                continue

            if response.silent or chat_id is None:
                continue
            send_message(token, chat_id, response.message)


if __name__ == "__main__":
    import os

    default_cfg = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "control_center.json",
    )
    cfg = sys.argv[1] if len(sys.argv) > 1 else default_cfg
    raise SystemExit(run(cfg))
