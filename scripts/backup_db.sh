#!/usr/bin/env bash
#
# Daily database backup for the production deployment.
# See docs/deployment/DEPLOYMENT.md.
#
# Not wired up automatically — install it as a cron entry on the server:
#   15 2 * * * /srv/MaterialInventory/scripts/backup_db.sh >> /srv/MaterialInventory/backups/backup.log 2>&1
#
# The database is the whole backup: the app has no FileField/ImageField
# anywhere, so nothing but Postgres holds state.
#
# WARNING: this writes to the same disk as the database it dumps, which
# protects against "someone deleted a job" and not at all against the disk
# dying. Copy backups/ to another machine as well — a pull from that machine is
# safer than a push from this one.

set -euo pipefail
# pipefail is not decoration here: without it a pg_dump that dies mid-stream
# still exits 0 through gzip, leaving a truncated archive that looks fine.

cd "$(dirname "$0")/.."   # cron's working directory is not the repo

ENV_FILE=.env.production
COMPOSE="docker compose --env-file $ENV_FILE -f docker-compose.prod.yml"

if [ ! -f "$ENV_FILE" ]; then
    echo "$(date -Is) FATAL: $ENV_FILE not found — run this from the deployment checkout" >&2
    exit 1
fi

# Read the two values rather than sourcing the file: `set -a; . .env.production`
# would have the shell expand a `$` inside a password, so this script and Django
# could end up disagreeing about the credentials.
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

# A dump of an empty or unreachable database still produces a small valid .gz.
# Fail loudly instead of letting a cron job report success every night.
SIZE=$(wc -c < "$ARCHIVE")
if [ "$SIZE" -lt 1000 ]; then
    echo "$(date -Is) FATAL: $ARCHIVE is only $SIZE bytes — treating as a failed dump" >&2
    exit 1
fi

echo "$(date -Is) OK $ARCHIVE ($SIZE bytes)"

# -name, so a -delete can never walk off into something that is not a backup.
find backups/ -name '*.sql.gz' -mtime +30 -delete
