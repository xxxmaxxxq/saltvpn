#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Восстановление базы из дампа.
#  ВНИМАНИЕ: текущие данные будут заменены. Скрипт спросит подтверждение.
#      bash scripts/restore-db.sh backups/db_20260917_040000.sql.gz
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

DUMP="${1:-}"
[[ -n "$DUMP" && -f "$DUMP" ]] || { echo "Использование: bash scripts/restore-db.sh <файл.sql.gz>"; exit 1; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
# shellcheck disable=SC1091
set -a; source .env; set +a

echo "Будет восстановлена база $POSTGRES_DB из файла $DUMP."
read -rp "Текущие данные будут перезаписаны. Продолжить? (yes/no) " answer
[[ "$answer" == "yes" ]] || { echo "Отменено"; exit 0; }

COMPOSE="docker compose -f docker/docker-compose.yml --env-file .env"
$COMPOSE stop api bot worker

gunzip -c "$DUMP" | $COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"

$COMPOSE start api bot worker
echo "[+] База восстановлена, сервисы запущены"
