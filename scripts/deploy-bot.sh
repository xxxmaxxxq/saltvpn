#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Управление ботом на VPS одной короткой командой.
#
#      bash scripts/deploy-bot.sh          # собрать и запустить
#      bash scripts/deploy-bot.sh logs     # смотреть логи
#      bash scripts/deploy-bot.sh restart  # перезапустить
#      bash scripts/deploy-bot.sh update   # обновить код с GitHub и пересобрать
#      bash scripts/deploy-bot.sh status   # что сейчас запущено
#      bash scripts/deploy-bot.sh stop     # остановить
#      bash scripts/deploy-bot.sh doctor   # диагностика окружения
#
#  Два момента, ради которых скрипт вообще существует:
#
#  1. Вызов без флагов. Длинные docker-команды ломаются при копировании из
#     браузера: обычный дефис "-" превращается в типографский минус "−",
#     и docker отвечает "unknown shorthand flag".
#  2. Разные окружения. Где-то есть "docker compose" (плагин v2), где-то
#     только старый "docker-compose", а где-то ни того, ни другого. Скрипт
#     определяет это сам и в крайнем случае обходится обычным docker run.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker/docker-compose.bot-only.yml"
ENV_FILE="$PROJECT_DIR/.env"
IMAGE_NAME="salt-bot"
CONTAINER_NAME="salt_bot"
VOLUME_NAME="salt_bot_data"
ACTION="${1:-up}"

cd "$PROJECT_DIR"

log()  { echo -e "\033[1;32m[+]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
fail() { echo -e "\033[1;31m[✗]\033[0m $*"; exit 1; }

# ── Проверка окружения ─────────────────────────────────────────────────────

command -v docker >/dev/null 2>&1 \
    || fail "Docker не установлен. Поставьте: curl -fsSL https://get.docker.com | sh"

# Диагностику пускаем всегда: она нужна как раз тогда, когда что-то не так
if [[ "$ACTION" != "doctor" ]]; then
    docker info >/dev/null 2>&1 \
        || fail "Docker установлен, но не запущен. Запустите: systemctl start docker"
fi

# Как именно на этой машине вызывается compose
detect_compose() {
    if docker compose version >/dev/null 2>&1; then
        echo "plugin"
    elif command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
        echo "standalone"
    else
        echo "none"
    fi
}
COMPOSE_MODE="$(detect_compose)"

# ── Настройки ──────────────────────────────────────────────────────────────

if [[ ! -f "$ENV_FILE" ]]; then
    for candidate in "$HOME/saltvpn/.env" "$HOME/.env"; do
        if [[ -f "$candidate" ]]; then
            log "Нашёл настройки в $candidate — копирую в проект"
            cp "$candidate" "$ENV_FILE"
            break
        fi
    done
fi
if [[ "$ACTION" != "doctor" ]]; then
    [[ -f "$ENV_FILE" ]] \
        || fail "Нет файла .env. Создайте его: cp .env.example .env && nano .env"
    grep -q '^BOT_TOKEN=.\+' "$ENV_FILE" \
        || fail "В .env не заполнен BOT_TOKEN"
fi

# ── Обёртки над двумя способами запуска ────────────────────────────────────

dc() {
    case "$COMPOSE_MODE" in
        plugin)     docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@" ;;
        standalone) docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@" ;;
    esac
}

# Запуск без compose: то же самое, но обычными командами docker
plain_up() {
    warn "compose не найден — запускаю обычным docker run (результат тот же)"
    log "Собираю образ $IMAGE_NAME…"
    docker build -t "$IMAGE_NAME" -f "$PROJECT_DIR/docker/Dockerfile.api" "$PROJECT_DIR"

    docker volume create "$VOLUME_NAME" >/dev/null
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

    log "Запускаю контейнер $CONTAINER_NAME…"
    docker run -d \
        --name "$CONTAINER_NAME" \
        --restart unless-stopped \
        --env-file "$ENV_FILE" \
        -e LOCAL_MODE=true \
        -e BOT_MODE=polling \
        -e SQLITE_PATH=/data/vpn_local.sqlite3 \
        -v "$VOLUME_NAME:/data" \
        "$IMAGE_NAME" \
        python -m app.bot.main >/dev/null
}

# ── Команды ────────────────────────────────────────────────────────────────

case "$ACTION" in
    up|start|"")
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            plain_up
        else
            log "Собираю образ и запускаю бота (первый раз это 2–4 минуты)…"
            dc up -d --build
        fi
        echo
        log "Готово. Последние строки лога:"
        sleep 5
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            docker logs --tail 20 "$CONTAINER_NAME"
        else
            dc logs --tail 20
        fi
        echo
        log "Смотреть логи дальше:  bash scripts/deploy-bot.sh logs"
        ;;

    logs)
        log "Логи (выход — Ctrl+C, бот продолжит работать)"
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            docker logs -f --tail 100 "$CONTAINER_NAME"
        else
            dc logs -f --tail 100
        fi
        ;;

    restart)
        log "Перезапускаю бота (подхватит правки в config/*.yml и .env)…"
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            docker restart "$CONTAINER_NAME"
        else
            dc restart
        fi
        ;;

    update)
        log "Забираю свежий код с GitHub…"
        git pull
        log "Пересобираю и перезапускаю…"
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            plain_up
        else
            dc up -d --build
        fi
        ;;

    status|ps)
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            docker ps --filter "name=$CONTAINER_NAME"
        else
            dc ps
        fi
        ;;

    stop|down)
        log "Останавливаю бота (база в томе остаётся)…"
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
        else
            dc down
        fi
        ;;

    doctor)
        echo "── Диагностика окружения ──────────────────────────────────"
        echo "docker:            $(command -v docker || echo 'не найден')"
        echo "версия docker:     $(docker --version 2>&1 | head -1)"
        echo "docker compose v2: $(docker compose version 2>&1 | head -1)"
        if command -v docker-compose >/dev/null 2>&1; then
            echo "docker-compose v1: $(docker-compose --version 2>&1 | head -1)"
        else
            echo "docker-compose v1: не установлен"
        fi
        echo "выбранный режим:   $COMPOSE_MODE"
        echo "файл настроек:     $ENV_FILE"
        echo "compose-файл:      $COMPOSE_FILE"
        status_line="$(docker ps -a --filter "name=$CONTAINER_NAME" --format '{{.Status}}' 2>/dev/null | head -1)"
        echo "контейнер:         ${status_line:-нет}"
        echo "───────────────────────────────────────────────────────────"
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            warn "compose не найден. Это не мешает: будет использован docker run."
            warn "Поставить плагин (необязательно): apt-get install -y docker-compose-plugin"
        fi
        ;;

    *)
        fail "Неизвестная команда: $ACTION. Доступны: up, logs, restart, update, status, stop, doctor"
        ;;
esac
