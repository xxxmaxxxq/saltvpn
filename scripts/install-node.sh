#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Установка VPN-НОДЫ (Marzban-node + Xray).
#  Нода принимает трафик пользователей; панель и биллинг живут на мастере.
#  Запуск на чистой Ubuntu от root:
#      curl -fsSL <raw-url>/install-node.sh | bash
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

NODE_DIR="/opt/marzban-node"
DATA_DIR="/var/lib/marzban-node"

log() { echo -e "\033[1;32m[+]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }

[[ $EUID -eq 0 ]] || { echo "Запустите от root"; exit 1; }

log "Обновляю систему и ставлю Docker…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq ca-certificates curl gnupg ufw

if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
fi

log "Настраиваю фаервол…"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp    >/dev/null   # SSH
ufw allow 443       >/dev/null   # боевой порт (TCP + UDP для Hysteria2)
ufw allow 8443/tcp  >/dev/null   # резервный порт
ufw allow 2053/tcp  >/dev/null   # резервный порт
ufw allow 62050/tcp >/dev/null   # связь с панелью
ufw allow 62051/tcp >/dev/null   # связь с панелью
ufw --force enable >/dev/null

log "Сетевой тюнинг (BBR + лимиты)…"
cat > /etc/sysctl.d/99-node-tuning.conf <<'SYSCTL'
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_fastopen = 3
fs.file-max = 1000000
SYSCTL
sysctl -p /etc/sysctl.d/99-node-tuning.conf >/dev/null

mkdir -p "$NODE_DIR" "$DATA_DIR"

cat > "$NODE_DIR/docker-compose.yml" <<'COMPOSE'
services:
  marzban-node:
    image: gozargah/marzban-node:latest
    container_name: marzban-node
    restart: always
    network_mode: host
    environment:
      SSL_CLIENT_CERT_FILE: /var/lib/marzban-node/ssl_client_cert.pem
      SERVICE_PROTOCOL: rest
    volumes:
      - /var/lib/marzban-node:/var/lib/marzban-node
COMPOSE

# Запуск без compose: в сборке Docker из репозитория Ubuntu плагина нет,
# а ради одного контейнера ставить его необязательно
cat > "$NODE_DIR/start.sh" <<'START'
#!/usr/bin/env bash
set -euo pipefail

CERT=/var/lib/marzban-node/ssl_client_cert.pem

if [[ ! -s "$CERT" ]]; then
    echo "[✗] Нет сертификата панели: $CERT"
    echo "    Возьмите его на мастере: bash scripts/deploy-master.sh node-cert"
    exit 1
fi
if ! head -1 "$CERT" | grep -q "BEGIN CERTIFICATE"; then
    echo "[✗] Файл сертификата повреждён: первая строка не BEGIN CERTIFICATE"
    exit 1
fi

docker rm -f marzban-node >/dev/null 2>&1 || true
docker run -d \
    --name marzban-node \
    --restart always \
    --network host \
    -e SSL_CLIENT_CERT_FILE=/var/lib/marzban-node/ssl_client_cert.pem \
    -e SERVICE_PROTOCOL=rest \
    -v /var/lib/marzban-node:/var/lib/marzban-node \
    gozargah/marzban-node:latest >/dev/null

sleep 3
docker ps --filter name=marzban-node --format '[+] {{.Names}}: {{.Status}}'
START
chmod +x "$NODE_DIR/start.sh"

touch "$DATA_DIR/ssl_client_cert.pem"
chmod 600 "$DATA_DIR/ssl_client_cert.pem"

log "Готово!"
echo
echo "Дальше:"
echo "  1. В панели Marzban: Nodes → Add Node → скопируйте сертификат"
echo "  2. Вставьте его сюда:  nano $DATA_DIR/ssl_client_cert.pem"
echo "  3. Запустите ноду:     bash $NODE_DIR/start.sh"
echo "  4. На мастере добавьте ноду в сервис:"
echo "     python -m app.cli add-node --code nl-1 --location nl --host <IP этой ноды>"
echo
warn "Порт 443 должен быть свободен: на ноде не должно быть nginx/apache."
