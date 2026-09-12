#!/usr/bin/env bash
#
# Deploys a commit of main to this server: backup, fast-forward, build, migrate,
# restart. The one procedure behind both ways of deploying — see "Deploying an
# update" in docs/deployment/DEPLOYMENT.md:
#
#   ./scripts/deploy.sh            # by hand: whatever origin/main points at
#   ./scripts/deploy.sh <sha>      # by hand: a specific commit of main
#
# .github/workflows/deploy.yml reaches it over SSH with a key that
# authorized_keys restricts to this script (`command="..."`), so from there the
# commit arrives in SSH_ORIGINAL_COMMAND rather than as an argument.

set -euo pipefail

cd "$(dirname "$0")/.."   # the forced command's working directory is $HOME

ENV_FILE=.env.production
COMPOSE="docker compose --env-file $ENV_FILE -f docker-compose.prod.yml"

# A private repository fetched over HTTPS would otherwise sit at a credential
# prompt nobody can answer, and the workflow would hang until it times out.
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

# Under a forced command SSH_ORIGINAL_COMMAND is whatever the client sent.
# Accept a full commit hash and nothing else: no ref, no option, no second
# command.
[[ $TARGET =~ ^[0-9a-f]{40}$ ]] || fail "expected a full 40-character commit hash"
git merge-base --is-ancestor "$TARGET" origin/main 2> /dev/null || fail "$TARGET is not a commit on origin/main"

HEAD=$(git rev-parse HEAD)
if [ "$TARGET" = "$HEAD" ]; then
    # Not an early exit: re-running a deploy whose migrate or restart failed
    # has to finish the job.
    log "already at $TARGET — rebuilding, migrating and restarting anyway"
elif git merge-base --is-ancestor "$TARGET" HEAD; then
    # Moving the code back does not move the schema back, so an older commit
    # can meet tables it does not understand. That is a decision for a person.
    fail "refusing to roll back from $HEAD to $TARGET — migrations are not reversed automatically; roll back by hand"
elif ! git merge-base --is-ancestor HEAD "$TARGET"; then
    fail "the checkout has commits that are not on main — resolve that by hand"
fi

# Before anything changes. Always worth it, and non-negotiable when the
# update carries a migration.
log "backing up"
./scripts/backup_db.sh

if [ "$TARGET" != "$HEAD" ]; then
    log "fast-forwarding $HEAD -> $TARGET"
    git merge --ff-only --quiet "$TARGET"
fi

# Build first, while the old container keeps serving: collectstatic runs here,
# and a broken build leaves the site exactly as it was.
log "building"
# shellcheck disable=SC2086  # $COMPOSE is a command line, word splitting wanted
$COMPOSE build web

# Migrate from a one-off container of the *new* image before swapping it in.
# A failed migration is rolled back by Postgres and leaves the old code
# serving; the window where old code meets the new schema is the few seconds
# until `up` below, the same window the manual order had the other way round.
log "migrating"
# shellcheck disable=SC2086
$COMPOSE run --rm web python manage.py migrate --noinput

log "restarting"
# shellcheck disable=SC2086
$COMPOSE up -d

log "deployed $(git rev-parse --short HEAD)"
