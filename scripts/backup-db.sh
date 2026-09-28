#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Бэкап базы (биллинг и пользователи) + базы панели Marzban.
#  Запуск вручную:  make backup
#  По расписанию:   0 4 * * * cd /opt/vpn-service/vpn-service-project && bash scripts/backup-db.sh
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
KEEP_DAYS=14
STAMP="$(date +%Y%m%d_%H%M%S)"

cd "$PROJECT_DIR"
# shellcheck disable=SC1091
set -a; source .env; set +a

mkdir -p "$BACKUP_DIR"
COMPOSE="docker compose -f docker/docker-compose.yml --env-file .env"

echo "[+] Дамп PostgreSQL…"
$COMPOSE exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
    | gzip > "$BACKUP_DIR/db_$STAMP.sql.gz"

echo "[+] Копия базы Marzban…"
docker cp vpn_marzban:/var/lib/marzban/db.sqlite3 "$BACKUP_DIR/marzban_$STAMP.sqlite3" 2>/dev/null \
    || echo "[!] База Marzban не найдена — пропускаю"

echo "[+] Удаляю бэкапы старше $KEEP_DAYS дней…"
find "$BACKUP_DIR" -type f -mtime +$KEEP_DAYS -delete

SIZE=$(du -h "$BACKUP_DIR/db_$STAMP.sql.gz" | cut -f1)
echo "[+] Готово: $BACKUP_DIR/db_$STAMP.sql.gz ($SIZE)"
echo
echo "Восстановление из дампа:"
echo "  gunzip -c $BACKUP_DIR/db_$STAMP.sql.gz | $COMPOSE exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB"
