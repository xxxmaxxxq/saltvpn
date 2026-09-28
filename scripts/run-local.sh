#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Локальный запуск бота на своём компьютере — без сервера, Docker,
#  Postgres, Redis и домена. Нужен только Python 3.12 и токен бота.
#
#      bash scripts/run-local.sh
#
#  Что работает: весь интерфейс бота (кнопки, тарифы, кабинет, пауза,
#  рефералы, админка). Что не работает: выдача реальных ключей VPN —
#  для неё нужна панель Marzban с нодами (см. docs/RUNBOOK.md).
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$PROJECT_DIR/apps/api-server"

cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
    echo "[!] Нет файла .env. Создаю из шаблона…"
    cp .env.example .env
    echo "[!] Откройте .env, впишите BOT_TOKEN и ADMIN_IDS, затем запустите скрипт снова."
    exit 1
fi

if ! grep -q '^LOCAL_MODE=true' .env; then
    echo "[+] Включаю локальный режим в .env"
    if grep -q '^LOCAL_MODE=' .env; then
        sed -i.bak 's/^LOCAL_MODE=.*/LOCAL_MODE=true/' .env
    else
        printf '\n# Локальный запуск: SQLite вместо Postgres, состояния в памяти\nLOCAL_MODE=true\nBOT_MODE=polling\n' >> .env
    fi
fi

cd "$API_DIR"

if [[ ! -d .venv ]]; then
    echo "[+] Создаю виртуальное окружение…"
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[+] Устанавливаю зависимости (первый раз это пара минут)…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo
echo "════════════════════════════════════════════════════════════"
echo " Бот запускается. Откройте его в Telegram и нажмите /start"
echo " Остановить: Ctrl+C"
echo "════════════════════════════════════════════════════════════"
echo

python -m app.bot.main
