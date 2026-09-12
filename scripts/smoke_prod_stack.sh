#!/usr/bin/env bash
#
# Smoke test of the production stack. Brings up docker-compose.prod.yml — db,
# web and the Caddy proxy, the way the server runs them — and checks what the
# test suite cannot see, because it runs without the image, the proxy or the
# production settings. Run by the `docker-build` job in .github/workflows/ci.yml.
#
# Every check stands for a failure that would otherwise first appear on the
# server:
#
#   - compose and Caddy both accept their config files;
#   - the image builds and the running app serves *hashed* static URLs, i.e. the
#     manifest collectstatic wrote is actually read (a missing one is a 500 on
#     every page, which the plain-storage test suite never exercises);
#   - `check --deploy` against .env.production.example reports exactly the one
#     known warning, security.W021 — runbook step 12, automated;
#   - HTTP redirects to HTTPS, and HTTPS through Caddy answers 200 with HSTS
#     instead of redirecting to itself — which it does if Django stops seeing
#     the request as HTTPS (Caddy's X-Forwarded-Proto, SECURE_PROXY_SSL_HEADER);
#   - a login POST carrying a browser's Origin header succeeds, and its session
#     authenticates the next request (the CSRF origin check, the Host header
#     through the proxy, Secure cookies over HTTPS).
#
# Deliberately NOT claimed: that removing FORWARDED_ALLOW_IPS from the compose
# file or emptying CSRF_TRUSTED_ORIGINS fails this. Both were tried and neither
# does — gunicorn 26 passes X-Forwarded-Proto through to Django from any peer
# (the allow-list only gates its own wsgi.url_scheme), and once Django sees
# HTTPS, `Origin: https://<host>` matches the request's own host.
#
# It writes .env.production into the checkout it runs in, so it refuses to run
# where one already exists — never run it in the server's deployment checkout.
# Locally, use a clean worktree:
#   git worktree add ../mi-smoke && ../mi-smoke/scripts/smoke_prod_stack.sh
# Needs Docker and free host ports 80 and 443. It runs under its own project
# name and deletes only that project's volumes, so a real
# materialinventory-prod stack on the same machine is never touched.

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

# -k throughout: for `localhost` Caddy issues from its own internal CA rather
# than Let's Encrypt, which is what lets the proxy run here unmodified.
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

# Built from the committed template, not from values invented here, so the
# `check --deploy` count below is a check on the template the runbook tells the
# server to copy. Only the placeholders are filled in. `tr -d '\r'` because a
# Windows checkout may carry the template with CRLF endings.
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

# Caddy needs a moment to issue its local certificate and gunicorn to boot;
# --retry-all-errors with --fail rides out the 502s in between.
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

# Sent the way a browser sends it: Django's CSRF check compares Origin with the
# scheme and host it believes the request has, so this is where a proxy that
# rewrites Host or drops X-Forwarded-Proto shows up.
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

echo "production stack smoke test passed"
