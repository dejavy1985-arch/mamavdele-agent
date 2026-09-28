#!/usr/bin/env bash
# Установка Клопа на чистый сервер Ubuntu 22.04 или 24.04 (например, Beget VPS в Риге).
#
# Запуск от root (одной командой, см. BEGET_TRIAL.md):
#   curl -fsSL <адрес этого файла на GitHub> -o install_server.sh && bash install_server.sh
#
# Что делает:
#   1. ставит python3, git, curl, bubblewrap (песочница для агентов);
#   2. создаёт пользователя klop без прав администратора, Клоп работает от него;
#   3. скачивает из GitHub только папку agent_control_center (код действующего агента
#      mamavdele-agent на сервер не попадает);
#   4. ставит Claude Code для пользователя klop;
#   5. проверяет песочницу; на Ubuntu 24.04 при необходимости разрешает её в AppArmor;
#   6. кладёт файл службы, но НЕ включает её: сначала пробный запуск.
# Ключи и токены скрипт не спрашивает и не хранит. Их вводят потом:
#   bash scripts/setup_secrets.sh
# Повторный запуск безопасен: обновит код и ничего не сотрёт.
set -euo pipefail

REPO_URL="${ACC_REPO_URL:-https://github.com/dejavy1985-arch/mamavdele-agent.git}"
BRANCH="${ACC_BRANCH:-claude/agent-control-center-telegram-c92k4k}"
U="${ACC_USER:-klop}"

say() { printf '\n== %s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
as_user() { runuser -u "$U" -- env HOME="$UHOME" PATH="$UHOME/.local/bin:/usr/local/bin:/usr/bin:/bin" "$@"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустите от root (на Beget VPS вы входите как root)."; exit 1; }
command -v apt-get >/dev/null || { echo "Нужен Ubuntu или Debian (apt-get)."; exit 1; }

say "1/6 Пакеты: python3, git, curl, bubblewrap"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 git curl ca-certificates bubblewrap >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "Нужен Python 3.10 или новее, на сервере $(python3 --version)."; exit 1; }

say "2/6 Пользователь $U (без прав администратора)"
if ! id "$U" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$U"
fi
UHOME="$(getent passwd "$U" | cut -d: -f6)"
chmod 700 "$UHOME"

say "3/6 Код Клопа из GitHub (только папка agent_control_center)"
SRC="$UHOME/klop"
if ! git ls-remote --exit-code --heads "$REPO_URL" "$BRANCH" >/dev/null 2>&1; then
    warn "Ветки $BRANCH нет (возможно, её уже слили). Беру основную ветку."
    BRANCH="$(git ls-remote --symref "$REPO_URL" HEAD | sed -n 's#^ref: refs/heads/\(.*\)\tHEAD#\1#p')"
fi
if [ -d "$SRC/.git" ]; then
    as_user git -C "$SRC" pull --ff-only --quiet
else
    as_user git clone --quiet --depth 1 --filter=blob:none --no-checkout \
        --branch "$BRANCH" "$REPO_URL" "$SRC"
    as_user git -C "$SRC" sparse-checkout set --no-cone '/agent_control_center/'
    as_user git -C "$SRC" checkout --quiet "$BRANCH"
fi
APP="$SRC/agent_control_center"
[ -f "$APP/acc/cli.py" ] || { echo "Не удалось скачать код Клопа в $APP."; exit 1; }
echo "Код: $APP (ветка $BRANCH, коммит $(as_user git -C "$SRC" rev-parse --short HEAD))"

say "4/6 Claude Code для пользователя $U"
claude_version() { as_user claude --version 2>/dev/null; }
if claude_version >/dev/null; then
    echo "Уже установлен: $(claude_version)"
else
    # pipefail: без него неудачное скачивание выглядело бы как успешная установка.
    as_user bash -c 'set -o pipefail; curl -fsSL https://claude.ai/install.sh | bash' \
        >/dev/null 2>&1 || true
    if claude_version >/dev/null; then
        echo "Установлен: $(claude_version)"
    else
        warn "Claude Code НЕ установился (не скачался с claude.ai или не запускается)."
        warn "Повторите позже: su - $U, затем: curl -fsSL https://claude.ai/install.sh | bash"
    fi
fi

say "5/6 Песочница bubblewrap"
probe() { as_user bwrap --unshare-all --die-with-parent --ro-bind / / true >/dev/null 2>&1; }
SANDBOX=ok
if ! probe; then
    FLAG=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
    if [ -r "$FLAG" ] && [ "$(cat "$FLAG")" = "1" ] && command -v apparmor_parser >/dev/null; then
        # Ubuntu 24.04 запрещает программам создавать изолированные пространства, если
        # для них нет профиля AppArmor. Разрешаем это только самой программе bwrap.
        echo "Ubuntu ограничивает песочницы: добавляю профиль AppArmor для bwrap."
        cat > /etc/apparmor.d/bwrap-klop <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>

profile bwrap-klop /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap-klop>
}
PROFILE
        apparmor_parser -r /etc/apparmor.d/bwrap-klop || warn "Профиль AppArmor не загрузился."
    fi
    probe || SANDBOX=fail
fi
if [ "$SANDBOX" = ok ]; then
    echo "Песочница работает."
else
    warn "Песочница НЕ работает. Агенты с обязательной песочницей не запустятся."
    warn "Пришлите вывод команды: su - $U -c 'bwrap --unshare-all --ro-bind / / true'"
fi

say "6/6 Настройки и служба"
as_user mkdir -p "$APP/var" "$APP/config"
chmod 700 "$APP/var" "$APP/config"
if command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
    sed -e "s#^User=.*#User=$U#" \
        -e "s#^WorkingDirectory=.*#WorkingDirectory=$APP#" \
        -e "s#^Environment=PATH=.*#Environment=PATH=$UHOME/.local/bin:/usr/local/bin:/usr/bin:/bin#" \
        "$APP/deploy/acc.service" > /etc/systemd/system/acc.service
    systemctl daemon-reload
    echo "Служба acc подготовлена, но НЕ включена (включают после пробного запуска)."
else
    echo "systemd не найден: службу настроите вручную (deploy/acc.service)."
fi

cat <<EOF

Установка закончена. Дальше:
  1. Перейти в пользователя Клопа:     su - $U
  2. Открыть папку:                   cd ~/klop/agent_control_center
  3. Ввести ключи (скрытый ввод):      bash scripts/setup_secrets.sh
  4. Пробный запуск:                  python3 -m acc.cli trial --telegram
Отчёт сохранится в var/trial_report.md, в нём нет ключей.
EOF
