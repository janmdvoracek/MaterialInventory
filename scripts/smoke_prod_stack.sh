#!/usr/bin/env bash
#
# Smoke test of docker-compose.prod.yml (db, web, Caddy), run by CI's
# `docker-build` job. Checks: both configs validate, hashed static files are
# served, `check --deploy` reports only W021, HTTP redirects to HTTPS, HTTPS
# answers 200 with HSTS, a login POST works through the proxy, and /admin/ is
# a 404 to anyone who is not logged in as an admin.
#
# Does NOT catch a removed FORWARDED_ALLOW_IPS or empty CSRF_TRUSTED_ORIGINS
# (both tested; both still pass).
#
# Writes .env.production here, so never run it in the server checkout. Locally:
#   git worktree add ../mi-smoke && ../mi-smoke/scripts/smoke_prod_stack.sh
# Needs Docker and free ports 80/443. Uses its own project name and volumes.

set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=.env.production
BASE=https://localhost
DC=(docker compose --project-name materialinventory-smoke --env-file "$ENV_FILE" -f docker-compose.prod.yml)

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

ok() {
    echo "ok: $*"
}

# -k: Caddy uses its internal CA for localhost.
http_status() {
    curl -k -s -o /dev/null -w '%{http_code}' "$@"
}

if [ -e "$ENV_FILE" ]; then
    fail "$ENV_FILE already exists here. This script writes its own and deletes it afterwards — run it in a clean checkout (see the header)."
fi

WORK=$(mktemp -d)

cleanup() {
    local status=$?
    if [ "$status" -ne 0 ]; then
        echo "--- container logs ---" >&2
        "${DC[@]}" logs --no-color --tail 80 >&2 || true
    fi
    "${DC[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$WORK" "$ENV_FILE"
    exit "$status"
}
trap cleanup EXIT

# From the committed template, so the checks cover it. `tr` strips CRLF.
tr -d '\r' < .env.production.example \
    | sed -e 's/inventar\.firma\.cz/localhost/g' \
        -e "s/^SECRET_KEY=\$/SECRET_KEY=$(openssl rand -hex 32)/" \
        -e "s/^DB_PASSWORD=\$/DB_PASSWORD=$(openssl rand -hex 16)/" \
    > "$ENV_FILE"
if ! grep -Eq '^SECRET_KEY=.+' "$ENV_FILE" || ! grep -Eq '^DB_PASSWORD=.+' "$ENV_FILE"; then
    fail ".env.production.example no longer has empty SECRET_KEY= / DB_PASSWORD= lines to fill in"
fi

"${DC[@]}" config --quiet
ok "compose accepts docker-compose.prod.yml"

"${DC[@]}" run --rm --no-deps proxy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
ok "Caddy accepts the Caddyfile"

"${DC[@]}" up --detach --build --wait --wait-timeout 180
"${DC[@]}" exec -T web python manage.py migrate --noinput > /dev/null
ok "stack is up and migrated"

report=$("${DC[@]}" exec -T web python manage.py check --deploy 2>&1) || true
echo "$report"
if ! grep -q 'security.W021' <<< "$report" || ! grep -q 'identified 1 issue ' <<< "$report"; then
    fail "check --deploy must report exactly one issue, security.W021 (see runbook step 12)"
fi
ok "check --deploy reports only W021"

# Ride out 502s while the certificate and gunicorn come up.
curl -k -s -S -o /dev/null --fail --retry 30 --retry-delay 2 --retry-all-errors "$BASE/login/" \
    || fail "the site never answered through the proxy"

read -r status location < <(curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://localhost/login/)
[[ $status == 30[178] && $location == https://localhost/* ]] \
    || fail "http://localhost/login/ answered '$status $location', not a redirect to HTTPS"
ok "HTTP redirects to HTTPS"

page="$WORK/login.html"
jar="$WORK/cookies"
status=$(curl -k -s -c "$jar" -b "$jar" -D "$WORK/headers" -o "$page" -w '%{http_code}' "$BASE/login/")
[ "$status" = 200 ] \
    || fail "GET /login/ over HTTPS answered $status — a 301 means Django does not see the request as HTTPS (X-Forwarded-Proto from Caddy, SECURE_PROXY_SSL_HEADER)"
grep -qi '^strict-transport-security:' "$WORK/headers" \
    || fail "no Strict-Transport-Security header — the HTTPS settings in .env.production did not reach Django"
ok "HTTPS through the proxy answers 200 with HSTS"

css=$(grep -o '/static/css/app\.[0-9a-f]\{12\}\.css' "$page" | head -n 1) || true
[ -n "$css" ] || fail "the login page links no hashed app.css — manifest storage is not active in the image"
[ "$(http_status "$BASE$css")" = 200 ] || fail "$css is linked but not served"
ok "hashed static files are served ($css)"

username=smoke-test
password=$(openssl rand -hex 16)
"${DC[@]}" exec -T -e SMOKE_USERNAME="$username" -e SMOKE_PASSWORD="$password" web python manage.py shell -c \
    "import os; from accounts.models import User; User.objects.create_user(os.environ['SMOKE_USERNAME'], password=os.environ['SMOKE_PASSWORD'])"

token=$(grep -o 'name="csrfmiddlewaretoken" value="[^"]*"' "$page" | sed -e 's/.*value="//' -e 's/"$//') || true
[ -n "$token" ] || fail "no CSRF token on the login page"

# With a browser's Origin header, to exercise the CSRF origin check.
read -r status location < <(curl -k -s -c "$jar" -b "$jar" -o /dev/null -w '%{http_code} %{redirect_url}\n' \
    -H "Origin: $BASE" -H "Referer: $BASE/login/" \
    --data-urlencode "csrfmiddlewaretoken=$token" \
    --data-urlencode "username=$username" \
    --data-urlencode "password=$password" \
    "$BASE/login/")
[ "$status" = 302 ] \
    || fail "login POST answered $status — 403 is the CSRF origin check (Origin vs. the scheme and host Django sees), 200 is refused credentials"
[ "$(http_status -b "$jar" "$BASE/")" = 200 ] || fail "logged in, but the session did not authenticate the next request"
ok "login POST through the proxy works"

# The gate is middleware, so the image carries it; this checks it survived the
# route through Caddy. The smoke user is a plain worker, hence the second call.
# The slashless /admin must 404 too: a 301 means the gate was moved below
# CommonMiddleware, whose APPEND_SLASH then confirms the admin is there.
for url in "$BASE/admin/" "$BASE/admin" "$BASE/admin/login/"; do
    [ "$(http_status "$url")" = 404 ] \
        || fail "$url is reachable without a session — AdminSessionRequiredMiddleware is not in MIDDLEWARE"
done
[ "$(http_status -b "$jar" "$BASE/admin/")" = 404 ] \
    || fail "/admin/ answered a logged-in non-admin — the gate must read has_admin_access, not is_authenticated"
ok "/admin/ is 404 for anonymous and non-admin requests"

echo "production stack smoke test passed"
