#!/usr/bin/env bash
# Ввод ключей для пробного запуска Клопа. Запускать на сервере от пользователя klop
# из папки agent_control_center:
#   bash scripts/setup_secrets.sh
#
# Ключи вводятся скрыто и сохраняются только на этом сервере, в файлах с правами 600.
# В git и в отчёт проверки они не попадают. Никому их не пересылайте, в чат тоже.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
PY="${PYTHON:-python3}"
TRIAL_ENV=homes/trial-claude/secrets/agent.env
mkdir -p homes/trial-claude/secrets config

echo
echo "Шаг 1 из 3. Доступ к Claude для ПРОБНОГО агента."
echo "  Вариант А: ключ API с сайта platform.claude.com (начинается с sk-ant-api)."
echo "  Вариант Б: подписка Claude Pro или Max. В другом окне выполните: claude setup-token"
echo "             и вставьте полученный токен (начинается с sk-ant-oat)."
read -rsp "  Вставьте ключ или токен (ввод не отображается; Enter, чтобы пропустить): " KEY
echo
if [ -n "$KEY" ]; then
    case "$KEY" in
        sk-ant-oat*) VAR=CLAUDE_CODE_OAUTH_TOKEN ;;
        *) VAR=ANTHROPIC_API_KEY ;;
    esac
    printf '%s=%s\n' "$VAR" "$KEY" > "$TRIAL_ENV"
    chmod 600 "$TRIAL_ENV"
    echo "  Сохранено в $TRIAL_ENV ($VAR)."
elif [ -f "$TRIAL_ENV" ]; then
    echo "  Оставлен прежний ключ."
else
    echo "  Пропущено: без ключа проверка Claude Code не пройдёт."
fi
unset KEY

echo
echo "Шаг 2 из 3. Токен НОВОГО бота Telegram."
echo "  В Telegram откройте @BotFather, отправьте /newbot, придумайте имя и адрес бота"
echo "  (адрес должен оканчиваться на bot). BotFather пришлёт токен вида 123456789:AA..."
echo "  Бот действующего агента не подходит: Клоп это проверит и откажется с ним работать."
read -rsp "  Вставьте токен (ввод не отображается; Enter, чтобы пропустить): " TG
echo
if [ -n "$TG" ]; then
    printf '%s\n' "$TG" > config/telegram_token.txt
    chmod 600 config/telegram_token.txt
    echo "  Сохранено в config/telegram_token.txt."
fi
unset TG

echo
echo "Шаг 3 из 3. Ваш номер в Telegram (user_id): бот будет принимать команды только от него."
echo "  Откройте своего нового бота в Telegram, нажмите Start и напишите любое слово."
read -rp "  Когда напишете, нажмите Enter... " _
"$PY" -m acc.cli telegram-id || true
read -rp "  Введите ваш user_id (число из строки выше; Enter, чтобы пропустить): " OWNER
case "$OWNER" in
    '') echo "  Пропущено. Позже: python3 -m acc.cli set-owner ВАШ_ЧИСЛОВОЙ_ID" ;;
    *[!0-9]*) echo "  Это не число. Позже: python3 -m acc.cli set-owner ВАШ_ЧИСЛОВОЙ_ID" ;;
    *) "$PY" -m acc.cli set-owner "$OWNER" ;;
esac

echo
echo "Готово. Пробный запуск:"
echo "  python3 -m acc.cli trial --telegram"
