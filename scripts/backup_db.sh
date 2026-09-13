#!/usr/bin/env bash
#
# Daily Postgres backup (the only state the app has). Install as a cron entry:
#   15 2 * * * /srv/MaterialInventory/scripts/backup_db.sh >> /srv/MaterialInventory/backups/backup.log 2>&1
#
# Writes to the same disk as the database — also copy backups/ off the server.

set -euo pipefail
# pipefail: otherwise a failed pg_dump still exits 0 through gzip.

cd "$(dirname "$0")/.."   # cron's working directory is not the repo

ENV_FILE=.env.production
COMPOSE="docker compose --env-file $ENV_FILE -f docker-compose.prod.yml"

if [ ! -f "$ENV_FILE" ]; then
    echo "$(date -Is) FATAL: $ENV_FILE not found — run this from the deployment checkout" >&2
    exit 1
fi

# grep, not `source`: sourcing would expand a `$` inside a value.
DB_USER=$(grep -E '^DB_USER=' "$ENV_FILE" | head -1 | cut -d= -f2-)
DB_NAME=$(grep -E '^DB_NAME=' "$ENV_FILE" | head -1 | cut -d= -f2-)

if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ]; then
    echo "$(date -Is) FATAL: DB_USER/DB_NAME missing from $ENV_FILE" >&2
    exit 1
fi

mkdir -p backups
ARCHIVE="backups/materialinventory_$(date +%F_%H%M).sql.gz"

# shellcheck disable=SC2086  # $COMPOSE is a command line, word splitting wanted
$COMPOSE exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$ARCHIVE"

# A failed dump can still produce a small valid .gz.
SIZE=$(wc -c < "$ARCHIVE")
if [ "$SIZE" -lt 1000 ]; then
    echo "$(date -Is) FATAL: $ARCHIVE is only $SIZE bytes — treating as a failed dump" >&2
    exit 1
fi

echo "$(date -Is) OK $ARCHIVE ($SIZE bytes)"

find backups/ -name '*.sql.gz' -mtime +30 -delete
