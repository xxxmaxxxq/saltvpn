#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Подготовка МАСТЕР-сервера: Docker, фаервол, fail2ban, рабочая папка.
#  Запуск на чистой Ubuntu 22.04/24.04 от root:
#      curl -fsSL <raw-url>/install-master.sh | bash
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/opt/vpn-service"

log() { echo -e "\033[1;32m[+]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }

[[ $EUID -eq 0 ]] || { echo "Запустите от root"; exit 1; }

log "Обновляю систему…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

log "Ставлю базовые пакеты…"
apt-get install -y -qq ca-certificates curl gnupg git ufw fail2ban htop

if ! command -v docker >/dev/null 2>&1; then
    log "Устанавливаю Docker…"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
else
    log "Docker уже установлен"
fi

log "Настраиваю фаервол (открыты только 22, 80, 443)…"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp  >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

log "Включаю fail2ban (защита SSH от перебора)…"
systemctl enable --now fail2ban

log "Увеличиваю сетевые лимиты для 1000+ подключений…"
cat > /etc/sysctl.d/99-vpn-tuning.conf <<'SYSCTL'
net.core.somaxconn = 8192
net.ipv4.tcp_max_syn_backlog = 8192
net.ipv4.tcp_fastopen = 3
net.ipv4.ip_forward = 1
fs.file-max = 1000000
SYSCTL
sysctl -p /etc/sysctl.d/99-vpn-tuning.conf >/dev/null

mkdir -p "$INSTALL_DIR"

log "Готово!"
echo
echo "Дальше:"
echo "  cd $INSTALL_DIR"
echo "  git clone https://github.com/xxxmaxxxq/my-life.git ."
echo "  cd vpn-service-project && cp .env.example .env && nano .env"
echo "  make up && make migrate"
echo
warn "Не забудьте настроить вход по SSH-ключу и отключить вход по паролю."
