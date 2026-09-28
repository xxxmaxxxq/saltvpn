#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Полный мастер-сервер: панель Marzban + бот + сайт + воркеры.
#
#      bash scripts/deploy-master.sh          # развернуть всё
#      bash scripts/deploy-master.sh logs     # логи
#      bash scripts/deploy-master.sh status   # что запущено
#      bash scripts/deploy-master.sh keys     # показать ключи Reality
#      bash scripts/deploy-master.sh stop     # остановить
#
#  Перед первым запуском в .env должны быть заполнены:
#      PUBLIC_BASE_URL   — https://vpn.ваш-домен
#      PANEL_BASE_URL    — https://panel.ваш-домен
#      MARZBAN_USERNAME, MARZBAN_PASSWORD — вход в панель
#  Обе A-записи должны указывать на этот сервер.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker/docker-compose.master.yml"
ENV_FILE="$PROJECT_DIR/.env"
GEN_DIR="$PROJECT_DIR/config/generated"
KEYS_FILE="$GEN_DIR/reality-keys.json"
XRAY_FILE="$GEN_DIR/xray_config.json"
TEMPLATE="$PROJECT_DIR/config/xray-vless/master-xray-config.template.json"

NODE_CODE="${NODE_CODE:-nl-1}"
NODE_LOCATION="${NODE_LOCATION:-nl}"
XRAY_PORT="${XRAY_PORT:-8443}"
XRAY_SNI="${XRAY_SNI:-www.nvidia.com}"

# Ноды для конфига Xray: код:порт:sni через пробел.
# Панель раздаёт один конфиг на все ноды, поэтому порты должны быть
# свободны на каждом сервере сразу. Значение берётся из окружения,
# затем из .env, и только потом падает на единственную ноду по умолчанию.
NODES="${NODES:-}"

ACTION="${1:-up}"
cd "$PROJECT_DIR"

log()  { echo -e "\033[1;32m[+]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
fail() { echo -e "\033[1;31m[✗]\033[0m $*"; exit 1; }

command -v docker >/dev/null 2>&1 || fail "Docker не установлен"

if docker compose version >/dev/null 2>&1; then
    dc() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
    dc() { docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }
else
    fail "Нужен docker compose: apt-get install -y docker-compose-v2"
fi

[[ -f "$ENV_FILE" ]] || fail "Нет файла .env"

read_env() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true; }
host_of()  { local u="${1#https://}"; u="${u#http://}"; echo "${u%%/*}"; }

PUBLIC_URL="$(read_env PUBLIC_BASE_URL)"
PANEL_URL="$(read_env PANEL_BASE_URL)"
MZ_USER="$(read_env MARZBAN_USERNAME)"
MZ_PASS="$(read_env MARZBAN_PASSWORD)"

[[ -n "$PUBLIC_URL" ]] || fail "В .env нет PUBLIC_BASE_URL"
[[ -n "$PANEL_URL"  ]] || fail "В .env нет PANEL_BASE_URL. Добавьте:
    echo 'PANEL_BASE_URL=https://panel.ваш-домен' >> .env"
[[ -n "$MZ_USER" && -n "$MZ_PASS" ]] || fail "В .env нет MARZBAN_USERNAME или MARZBAN_PASSWORD"

if [[ -z "$NODES" ]]; then
    NODES="$(read_env NODES)"
fi
NODES="${NODES:-$NODE_CODE:$XRAY_PORT:$XRAY_SNI}"

PUBLIC_HOST="$(host_of "$PUBLIC_URL")"
PANEL_HOST="$(host_of "$PANEL_URL")"
SERVER_IP="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || echo '')"

check_dns() {
    local name="$1" resolved
    resolved="$(getent hosts "$name" 2>/dev/null | awk '{print $1}' | head -1)"
    if [[ -z "$resolved" ]]; then
        warn "Домен $name не резолвится — сертификат не выпустится, пока не разойдётся DNS"
    elif [[ -n "$SERVER_IP" && "$resolved" != "$SERVER_IP" ]]; then
        warn "Домен $name ведёт на $resolved, а сервер — $SERVER_IP. Исправьте A-запись."
    else
        log "Домен $name указывает на этот сервер"
    fi
}

render_config() {
    mkdir -p "$GEN_DIR"

    local node_args=()
    for node in $NODES; do
        node_args+=(--node "$node")
    done

    log "Собираю конфиг Xray для нод: $NODES"
    # --user root: внутри образа мы работаем от appuser, а каталог
    # config/generated на хосте принадлежит root — без этого флага
    # генератор падает с PermissionError на reality-keys.json.
    dc run --rm --no-deps -T --user root \
        -v "$GEN_DIR:/generated" \
        api python -m app.cli render-xray-config \
        "${node_args[@]}" \
        --keys-file /generated/reality-keys.json \
        --output /generated/xray_config.json

    python3 -c "import json;json.load(open('$XRAY_FILE'))" \
        || fail "Сгенерированный конфиг Xray невалиден"
    chmod 600 "$KEYS_FILE" 2>/dev/null || true
}

ensure_fresh_image() {
    log "Проверяю, что контейнеры собраны из текущего кода…"
    dc up -d --build api bot worker >/dev/null 2>&1 || dc up -d --build >/dev/null
}

wait_for_panel() {
    log "Жду, пока панель поднимется…"
    for _ in $(seq 1 30); do
        if dc exec -T api python -c "
import asyncio, sys
from app.marzban import MarzbanClient, MarzbanError
async def main():
    try:
        async with MarzbanClient() as mz:
            await mz.list_inbounds()
    except MarzbanError:
        sys.exit(1)
asyncio.run(main())
" >/dev/null 2>&1; then
            log "Панель отвечает"
            return 0
        fi
        sleep 4
    done
    warn "Панель не ответила за 2 минуты. Логи: bash scripts/deploy-master.sh logs"
    return 1
}

case "$ACTION" in
    up|start|"")
        check_dns "$PUBLIC_HOST"
        check_dns "$PANEL_HOST"

        log "Собираю образы…"
        dc build

        render_config

        # Конфиг кладём в том ДО первого старта: если панель не найдёт файл,
        # на который указывает XRAY_JSON, ядро Xray не поднимется вовсе
        log "Кладу конфиг Xray в том панели…"
        docker volume inspect salt_marzban_data >/dev/null 2>&1 \
            || docker volume create salt_marzban_data >/dev/null
        docker run --rm \
            -v salt_marzban_data:/dst \
            -v "$GEN_DIR":/src:ro \
            alpine:3 sh -c 'cp /src/xray_config.json /dst/xray_config.json' >/dev/null

        log "Запускаю панель, бота, API, воркеры и Caddy…"
        dc up -d
        sleep 15

        if wait_for_panel; then
            log "Прописываю ноду в панели и в базе сервиса…"
            dc exec -T api python -m app.cli setup-marzban \
                --node-code "$NODE_CODE" \
                --location "$NODE_LOCATION" \
                --host "${SERVER_IP:-$PUBLIC_HOST}" \
                --port "$XRAY_PORT" \
                --sni "$XRAY_SNI" || warn "Не удалось настроить ноду автоматически"
        fi

        echo
        dc ps
        echo
        log "Панель: $PANEL_URL   логин: $MZ_USER"
        log "Проверка сайта: curl $PUBLIC_URL/health"
        log "Ключи Reality лежат в $KEYS_FILE (в git не попадают)"
        ;;

    node-cert)
        # Пересобираем образ: после git pull код на диске новее, чем в
        # работающем контейнере, и новых команд CLI там просто нет
        ensure_fresh_image
        # Сертификат панели — его нужно положить на сервер новой ноды
        dc exec -T api python -m app.cli node-cert
        ;;

    add-node)
        # bash scripts/deploy-master.sh add-node ru-1 ru <RU_NODE_HOST> 2053 www.samsung.com
        NEW_CODE="${2:?Укажите код ноды, например ru-1}"
        NEW_LOCATION="${3:?Укажите локацию: nl или ru}"
        NEW_HOST="${4:?Укажите IP сервера ноды}"
        NEW_PORT="${5:-2053}"
        NEW_SNI="${6:-www.samsung.com}"

        ensure_fresh_image

        log "Добавляю inbound для $NEW_CODE в конфиг Xray…"
        NODES="$NODES $NEW_CODE:$NEW_PORT:$NEW_SNI"
        render_config

        log "Обновляю конфиг в панели…"
        docker run --rm -v salt_marzban_data:/dst -v "$GEN_DIR":/src:ro \
            alpine:3 sh -c 'cp /src/xray_config.json /dst/xray_config.json' >/dev/null
        docker restart salt_marzban >/dev/null
        sleep 15
        wait_for_panel || fail "Панель не поднялась после обновления конфига"

        log "Регистрирую ноду в панели…"
        dc exec -T api python -m app.cli add-marzban-node \
            --name "$NEW_CODE" --address "$NEW_HOST"

        log "Прописываю host и запись в базе сервиса…"
        dc exec -T api python -m app.cli setup-marzban \
            --node-code "$NEW_CODE" --location "$NEW_LOCATION" \
            --host "$NEW_HOST" --port "$NEW_PORT" --sni "$NEW_SNI"

        echo
        warn "Не забудьте добавить ноду в постоянный список, чтобы конфиг не терялся:"
        echo "    echo 'NODES=\"$NODES\"' >> .env"
        ;;

    relabel|refresh-hosts)
        # Переписать подписи профилей в панели (флаг + страна вместо логина)
        ensure_fresh_image
        dc exec -T api python -m app.cli refresh-hosts
        ;;

    logs)    dc logs -f --tail 100 ;;
    status|ps) dc ps ;;
    restart) dc restart ;;
    keys)
        [[ -f "$KEYS_FILE" ]] || fail "Ключи ещё не сгенерированы"
        cat "$KEYS_FILE"
        ;;
    update)
        git pull
        dc up -d --build
        ;;
    stop|down) dc down ;;
    *) fail "Неизвестная команда: $ACTION. Доступны: up, logs, status, restart, keys, update, stop, node-cert, add-node, relabel" ;;
esac
