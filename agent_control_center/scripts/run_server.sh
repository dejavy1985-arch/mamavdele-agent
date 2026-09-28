#!/usr/bin/env bash
# Запуск центра управления на Linux-сервере (например, VPS на Beget).
#
#   bash scripts/run_server.sh --check   # тесты + самопроверка, бот НЕ запускается
#   bash scripts/run_server.sh           # запуск Telegram-центра (нужен токен)
#
# Токен: переменная окружения ACC_TELEGRAM_BOT_TOKEN или файл
# config/telegram_token.txt (права 600, в git не попадает).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    echo "Нужен Python 3.10 или новее (сейчас: $("$PY" --version 2>&1 || echo 'не найден'))." >&2
    exit 1
fi

if [ "${1:-}" = "--check" ]; then
    "$PY" tests/run_tests.py
    "$PY" -m acc.cli doctor
    exit 0
fi

if [ ! -f config/control_center.json ]; then
    echo "Нет config/control_center.json. Скопируйте config/control_center.example.json" >&2
    echo "в config/control_center.json и впишите свой Telegram user_id." >&2
    exit 1
fi

exec "$PY" -m acc.telegram_bot config/control_center.json
