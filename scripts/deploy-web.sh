#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Запуск связки «бот + сайт подписки» (нужен домен).
#
#      bash scripts/deploy-web.sh          # собрать и запустить
#      bash scripts/deploy-web.sh logs     # логи всех контейнеров
#      bash scripts/deploy-web.sh check    # проверить домен и сертификат
#      bash scripts/deploy-web.sh stop     # остановить
#
#  Перед первым запуском:
#    1. Купить домен и направить A-запись на IP этого сервера
#    2. В .env указать PUBLIC_BASE_URL=https://ваш-домен
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker/docker-compose.bot-web.yml"
ENV_FILE="$PROJECT_DIR/.env"
ACTION="${1:-up}"

cd "$PROJECT_DIR"

log()  { echo -e "\033[1;32m[+]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
fail() { echo -e "\033[1;31m[✗]\033[0m $*"; exit 1; }

command -v docker >/dev/null 2>&1 \
    || fail "Docker не установлен: curl -fsSL https://get.docker.com | sh"

# Три контейнера удобнее поднимать через compose, но он есть не везде:
# в сборке Docker из репозитория Ubuntu плагина нет. Поэтому предусмотрен
# путь на голых docker-командах — результат тот же.
COMPOSE_MODE="none"
if docker compose version >/dev/null 2>&1; then
    COMPOSE_MODE="plugin"
    dc() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }
elif command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
    COMPOSE_MODE="standalone"
    dc() { docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }
fi

IMAGE_NAME="salt-bot"
NETWORK_NAME="salt_net"

plain_up() {
    warn "compose не найден — поднимаю контейнеры обычными docker-командами"

    log "Собираю образ $IMAGE_NAME…"
    docker build -t "$IMAGE_NAME" -f "$PROJECT_DIR/docker/Dockerfile.api" "$PROJECT_DIR"

    docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 \
        || docker network create "$NETWORK_NAME" >/dev/null
    for vol in salt_bot_data caddy_data caddy_config; do
        docker volume inspect "$vol" >/dev/null 2>&1 || docker volume create "$vol" >/dev/null
    done
    docker rm -f salt_bot salt_api salt_caddy >/dev/null 2>&1 || true

    log "Запускаю бота…"
    docker run -d --name salt_bot --restart unless-stopped \
        --network "$NETWORK_NAME" \
        --env-file "$ENV_FILE" \
        -e LOCAL_MODE=true -e BOT_MODE=polling -e SQLITE_PATH=/data/vpn_local.sqlite3 \
        -v salt_bot_data:/data \
        "$IMAGE_NAME" python -m app.bot.main >/dev/null

    log "Запускаю API…"
    # Сетевой алиас api обязателен: именно это имя ждёт Caddyfile.web
    docker run -d --name salt_api --restart unless-stopped \
        --network "$NETWORK_NAME" --network-alias api \
        --env-file "$ENV_FILE" \
        -e LOCAL_MODE=true -e SQLITE_PATH=/data/vpn_local.sqlite3 \
        -v salt_bot_data:/data \
        "$IMAGE_NAME" \
        uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers >/dev/null

    log "Запускаю Caddy (выпуск сертификата)…"
    docker run -d --name salt_caddy --restart unless-stopped \
        --network "$NETWORK_NAME" \
        -p 80:80 -p 443:443 \
        -e DOMAIN="$DOMAIN_URL" \
        -v "$PROJECT_DIR/docker/Caddyfile.web:/etc/caddy/Caddyfile:ro" \
        -v caddy_data:/data -v caddy_config:/config \
        caddy:2-alpine >/dev/null
}

plain_logs()    { docker logs -f --tail 100 salt_bot; }
plain_status()  { docker ps --filter "name=salt_" ; }
plain_stop()    { docker rm -f salt_bot salt_api salt_caddy >/dev/null 2>&1 || true; }

[[ -f "$ENV_FILE" ]] || fail "Нет файла .env"

# "|| true" здесь обязателен: при set -euo pipefail отсутствие строки
# в .env убивало скрипт молча, без единого сообщения
DOMAIN_URL="$(grep -E '^PUBLIC_BASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2- \
    | tr -d '"' | tr -d "'" || true)"

if [[ -z "$DOMAIN_URL" ]]; then
    fail "В .env нет строки PUBLIC_BASE_URL. Добавьте её:
    echo 'PUBLIC_BASE_URL=https://ваш-домен' >> .env"
fi
DOMAIN="${DOMAIN_URL#https://}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN%%/*}"

check_domain() {
    [[ -n "$DOMAIN" && "$DOMAIN" != "localhost:8000" && "$DOMAIN" != localhost* ]] \
        || fail "В .env не указан домен. Впишите PUBLIC_BASE_URL=https://ваш-домен"

    [[ "$DOMAIN_URL" == https://* ]] \
        || fail "PUBLIC_BASE_URL должен начинаться с https:// — иначе Telegram не примет кнопку"

    log "Домен: $DOMAIN"

    local server_ip domain_ip
    server_ip="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || echo '')"
    domain_ip="$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -1)"

    if [[ -z "$domain_ip" ]]; then
        warn "Домен $DOMAIN пока не резолвится. DNS-запись обновляется до 15 минут."
        warn "Запуск продолжу, но сертификат выпустится только после появления записи."
    elif [[ -n "$server_ip" && "$domain_ip" != "$server_ip" ]]; then
        warn "A-запись домена ведёт на $domain_ip, а IP сервера — $server_ip."
        warn "Исправьте A-запись у регистратора, иначе сертификат не выпустится."
    else
        log "A-запись домена указывает на этот сервер ($domain_ip) — верно"
    fi

    if command -v ss >/dev/null 2>&1 && ss -lntp 2>/dev/null | grep -qE ':(80|443)\s'; then
        warn "Порты 80/443 уже кем-то заняты (nginx, apache?). Caddy не сможет их взять."
    fi
}

case "$ACTION" in
    up|start|"")
        check_domain
        # Режим «только бот» мог оставить контейнер с тем же именем —
        # compose на этом падает с конфликтом имён
        if docker ps -a --format '{{.Names}}' | grep -qx "salt_bot"; then
            if ! docker inspect salt_bot --format '{{index .Config.Labels "com.docker.compose.project"}}' \
                | grep -q .; then
                log "Убираю контейнер из режима «только бот» (база в томе сохраняется)…"
                docker rm -f salt_bot >/dev/null
            fi
        fi
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            plain_up
        else
            log "Собираю и запускаю бота, API и Caddy…"
            dc up -d --build
        fi
        log "Жду выпуск сертификата (до минуты)…"
        sleep 25
        if [[ "$COMPOSE_MODE" == "none" ]]; then plain_status; else dc ps; fi
        echo
        if curl -fsS --max-time 15 "https://$DOMAIN/health" >/dev/null 2>&1; then
            log "Сайт отвечает по https — кнопка «Подключить в 1 клик» заработает"
        else
            warn "https пока не отвечает. Посмотрите логи: bash scripts/deploy-web.sh logs"
            warn "Частые причины: A-запись не обновилась, порты 80/443 заняты."
        fi
        ;;
    logs)
        if [[ "$COMPOSE_MODE" == "none" ]]; then plain_logs; else dc logs -f --tail 100; fi
        ;;
    check)
        check_domain
        echo
        log "Проверяю https…"
        curl -fsS --max-time 15 "https://$DOMAIN/health" && echo || warn "https не отвечает"
        ;;
    restart)
        if [[ "$COMPOSE_MODE" == "none" ]]; then
            docker restart salt_bot salt_api salt_caddy
        else
            dc restart
        fi
        ;;
    update)
        git pull
        if [[ "$COMPOSE_MODE" == "none" ]]; then plain_up; else dc up -d --build; fi
        ;;
    status|ps)
        if [[ "$COMPOSE_MODE" == "none" ]]; then plain_status; else dc ps; fi
        ;;
    stop|down)
        if [[ "$COMPOSE_MODE" == "none" ]]; then plain_stop; else dc down; fi
        ;;
    *)
        fail "Неизвестная команда: $ACTION. Доступны: up, logs, check, restart, update, status, stop"
        ;;
esac
