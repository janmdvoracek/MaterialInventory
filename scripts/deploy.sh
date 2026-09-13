#!/usr/bin/env bash
#
# Deploy a commit of main: backup, fast-forward, build, migrate, restart.
#
#   ./scripts/deploy.sh            # origin/main
#   ./scripts/deploy.sh <sha>      # a specific commit of main
#
# deploy.yml calls it over SSH as a forced command, passing the hash in
# SSH_ORIGINAL_COMMAND.

set -euo pipefail

cd "$(dirname "$0")/.."   # a forced command starts in $HOME

ENV_FILE=.env.production
COMPOSE="docker compose --env-file $ENV_FILE -f docker-compose.prod.yml"

# Fail instead of hanging on a credential prompt.
export GIT_TERMINAL_PROMPT=0

log() {
    echo "$(date -Is) $*"
}

fail() {
    echo "$(date -Is) FATAL: $*" >&2
    exit 1
}

[ -f "$ENV_FILE" ] || fail "$ENV_FILE not found — run this from the deployment checkout"
[ "$(git symbolic-ref --short -q HEAD)" = main ] || fail "the checkout is not on branch main"

git fetch --quiet origin main

TARGET=${SSH_ORIGINAL_COMMAND:-${1:-}}
if [ -z "$TARGET" ]; then
    TARGET=$(git rev-parse origin/main)
fi

# SSH_ORIGINAL_COMMAND is client-controlled: accept a full hash only.
[[ $TARGET =~ ^[0-9a-f]{40}$ ]] || fail "expected a full 40-character commit hash"
git merge-base --is-ancestor "$TARGET" origin/main 2> /dev/null || fail "$TARGET is not a commit on origin/main"

HEAD=$(git rev-parse HEAD)
if [ "$TARGET" = "$HEAD" ]; then
    # Carry on, so a half-finished deploy can be completed.
    log "already at $TARGET — rebuilding, migrating and restarting anyway"
elif git merge-base --is-ancestor "$TARGET" HEAD; then
    # Migrations don't roll back with the code.
    fail "refusing to roll back from $HEAD to $TARGET — migrations are not reversed automatically; roll back by hand"
elif ! git merge-base --is-ancestor HEAD "$TARGET"; then
    fail "the checkout has commits that are not on main — resolve that by hand"
fi

log "backing up"
./scripts/backup_db.sh

if [ "$TARGET" != "$HEAD" ]; then
    log "fast-forwarding $HEAD -> $TARGET"
    git merge --ff-only --quiet "$TARGET"
fi

# Build and migrate from the new image while the old container keeps serving,
# so a failure leaves the site as it was.
log "building"
# shellcheck disable=SC2086  # $COMPOSE is a command line, word splitting wanted
$COMPOSE build web

log "migrating"
# shellcheck disable=SC2086
$COMPOSE run --rm web python manage.py migrate --noinput

log "restarting"
# shellcheck disable=SC2086
$COMPOSE up -d

log "deployed $(git rev-parse --short HEAD)"
