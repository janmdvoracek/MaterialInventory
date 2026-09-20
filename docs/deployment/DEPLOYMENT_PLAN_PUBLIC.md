# Publish on the company domain (VPS, HTTPS) — plan

Supersedes the network half of [DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md), which
planned a LAN-only deployment on an office server reached by IP over plain HTTP.
That plan's reasoning about the image, static files, `.gitignore`, seeding,
collation, logging and backups is all still current and is not repeated here —
only what the move to a public VPS and a real domain changes.

The runbook you actually follow is [DEPLOYMENT.md](DEPLOYMENT.md). This file is
why it looks the way it does.

## Context

The LAN plan made one assumption that did a lot of quiet work: **the network was
the perimeter.** Reaching the app at all required being in the building, so
plain HTTP was acceptable, an unlimited login form was acceptable, `/admin/` on
the open port was acceptable, and the "remote access" answer was a VPN
([DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md#remote-access-off-lan)) rather than
exposure.

Hosting moved to a VPS behind the company domain. That is a better answer to the
problems the LAN plan struggled with — no DHCP reservation to chase, no
dependence on the office router, a real static IP, and a name people can type —
but it removes the perimeter, and four of the LAN plan's deliberate omissions
were leaning on it.

**No application code changes.** No models, no migrations, no views, no
templates; the test suite is untouched. Everything below is configuration,
infrastructure and documentation.

## Approach

**TLS terminated by Caddy in front of gunicorn, in the same compose stack.**
Caddy over nginx+certbot because issue *and renewal* are built in: no certbot
sidecar, no renewal cron entry, no reload hook, and nothing that expires
silently 90 days after the person who set it up stops looking. The whole config
is a nine-line `Caddyfile`.

**Gunicorn stops being publicly reachable at all.** The LAN file published
`web` on the host and used `APP_BIND_IP` to pin it to the depot interface,
because Docker's published ports bypass `ufw` and `0.0.0.0` would have meant
"reachable from wherever this server is". On a VPS there is no LAN interface to
retreat to, so the answer is to publish nothing: `web` gets `expose: 8000` and is
reachable only over the Compose network, and the proxy owns 80/443. `APP_BIND_IP`
and `APP_PORT` are gone — there is no longer a port to bind or choose.

That single change is also what makes two other settings safe, and it is the
thing to remember before ever re-adding a `ports:` line to `web`:

- `SECURE_PROXY_SSL_HEADER` makes Django trust `X-Forwarded-Proto`. Caddy
  overwrites that header on every request, so it cannot be forged *through* the
  proxy — but anything that could reach gunicorn directly could set it freely.
- `FORWARDED_ALLOW_IPS: "*"` on the `web` service tells gunicorn to accept
  `X-Forwarded-*` from any peer, which is only reasonable when exactly one peer
  can connect.

**`FORWARDED_ALLOW_IPS` is the non-obvious one and deserves its own paragraph.**
Gunicorn discards `X-Forwarded-*` from any peer not in that list, and its default
is `127.0.0.1` — which the proxy is *not*, being a separate container with its
own address on the Compose network. Left at the default, the header never
reaches Django, `SECURE_PROXY_SSL_HEADER` never fires, `request.is_secure()`
stays `False`, and `SECURE_SSL_REDIRECT` redirects every already-HTTPS request
back to HTTPS forever. The symptom is an infinite redirect loop that looks like
a proxy misconfiguration, and the fix is in neither the proxy nor Django.

**The HTTPS settings are env-driven and default to off.** `config/settings.py`
gains `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`,
`SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS` and
`SECURE_HSTS_PRELOAD`, every one of them reading `python-decouple` with an
off-by-default value. This is not caution for its own sake — it is the same trap
`STATICFILES_BACKEND` already carries in that file, for the same reason. Django's
test runner **forces `DEBUG=False`**, so a hardcoded `SECURE_SSL_REDIRECT = True`
turns every `self.client.get()` in the suite into a 301 and fails essentially
every test in the project. Production opts in through `.env.production`; dev, CI
and `runserver` never see them.

`SECURE_PROXY_SSL_HEADER` is the exception and is set unconditionally, because
it is inert unless the header is actually present.

**`CSRF_TRUSTED_ORIGINS` stops being optional**, exactly as
`config/settings.py` and [docs/configuration.md](../configuration.md) already
predicted it would the moment anything terminated TLS in front of Django. The
browser sends `Origin: https://<domain>`; Django, reached over plain HTTP by the
proxy, expects `http://<domain>`; they differ by scheme and **every POST fails
403 with "Origin checking failed"** — login included — while every page still
loads normally. It is the single most likely thing to be got wrong here, which
is why the runbook makes logging in step 13's explicit CSRF check rather than a
step that happens to pass.

**HSTS starts at an hour, not a year.** It is the only setting in this change
that cannot be undone by editing a file: a browser that has seen the header
refuses plain HTTP for the host until the max-age expires, regardless of what
the server later sends. A year of that on a domain whose certificate has broken
is a year of an unreachable app. `.env.production.example` ships `3600` with a
note to raise it to `31536000` after a renewal has actually happened.

`SECURE_HSTS_INCLUDE_SUBDOMAINS` is on and is safe **because the app sits on a
subdomain** — it covers `inventar.firma.cz` and anything beneath, and does not
propagate up to `firma.cz`. Deployed at the apex it would commit every one of
the company's subdomains to HTTPS, which is a policy decision rather than a
checkbox, and the setting's comment says so.

`SECURE_HSTS_PRELOAD` is deliberately left off, so `manage.py check --deploy`
reports **exactly one** warning (`W021`) rather than zero. Preloading submits
the domain to a list compiled into browsers themselves; removal takes months and
reaches users only as they update. Runbook step 12 states the expected count, in
the same spirit as the LAN runbook's "expect exactly four".

**`down -v` is no longer a safe instruction.** The LAN runbook used it twice —
to re-initialise a wrongly-collated cluster and to upgrade Postgres — when the
only volume was `db_data`. There are now three, and `caddy_data` holds the
certificate and the ACME account key. Destroying it means a fresh issue against
a **rate limit of 5 certificates per exact hostname per week**; do it a few times
while debugging and the site has no certificate and no way to get one until the
window rolls. Both procedures now name `materialinventory-prod_db_data`
explicitly.

## What the LAN plan got that this does not need

- **The DHCP reservation** (LAN step 2). A VPS has a static public IP, and the
  address people bookmark is a DNS name either way. Replaced by an A record,
  which has to exist *before* the first `up` because Caddy validates over port
  80 — a stricter ordering constraint than the one it replaces.
- **`APP_PORT` and the `ss -tlnp` survey** (LAN step 1). The old server already
  ran another web app and another Postgres, so a free high port had to be found
  and the DB kept off the host entirely. On a dedicated VPS the proxy takes 80
  and 443 and nothing contends. Keeping Postgres unpublished stays correct for a
  much better reason than before: on this host, published means published to the
  internet.
- **The Tailscale section.** Its whole purpose was reaching a LAN-only app from
  outside; the app is now reachable by design. The narrower question it would
  still have answered — keeping `/admin/` off the open internet — is answered in
  the app instead (see below), so a VPN and the `Caddyfile`'s commented
  source-IP `route` block are both optional extra layers now rather than the
  only mitigations.

## Security posture — what the perimeter was carrying

The one part of this change that is not configuration. Ranked by how much it
matters once the login form is public:

1. **Login rate limiting — done, with `django-axes`.** Django ships none, and
   without it password guessing against every seeded account would be unlimited
   and silent. Five failures lock the **account** for 30 minutes, cleared by a
   successful login.

   Locking by username rather than by address is the whole decision here, and
   the library's default (`['ip_address']`) would have been actively harmful:
   the depot reaches the app through one office NAT address, and behind the
   proxy every request axes has not been told about looks like it came from the
   proxy container — so one worker's typo would lock out everyone. What that
   costs is an attacker getting five guesses per account per cool-off from as
   many addresses as they like, which is the cheaper of the two failure modes.
   Axes disagrees loudly (`axes.W006`); the objection is answered in
   `SILENCED_SYSTEM_CHECKS` with its reasoning rather than ignored, which also
   keeps `check --deploy` at the single expected warning.

   Three consequences that are not obvious from the settings block. **It adds
   two models, so this change needs `migrate`** — without it the first login
   *or logout* is a 500 (`relation "axes_accessattempt" does not exist`), and
   the logout path is the one that surprises you. **`AXES_ENABLE_ADMIN` is
   off**: axes ships catalogs for ar/de/fa/fr/id/pl/ru/tr and no Czech, so its
   section and both its models would be the only English in an admin this
   project keeps Czech through three separate mechanisms; the log is read with
   `manage.py axes_list_attempts` instead. And **`AxesStandaloneBackend`
   defines no `get_user`**, which is what leaves the suite alone —
   `Client.force_login()` picks the first backend that has one, so all ~130
   `force_login` calls in the project resolve to `ModelBackend`. There is not a
   single `Client.login()` in the suite, and there must not be: it calls
   `authenticate()` with no request, which axes rejects outright.
2. **Seeded temporary passwords.** `seed_data` prints one random password per
   user; any that were handed out informally and never changed are now
   internet-facing credentials. The runbook says to treat them that way.
3. **`/admin/` on the open internet — done, in the app.** It was the superuser
   surface and the only page where guessing a password was worth an attacker's
   time, sitting at the URL every scanner tries first.
   `accounts/middleware.py::AdminSessionRequiredMiddleware` now answers **404**
   for the whole prefix unless the request already carries an admin's session.

   Covering `/admin/login/` is the point of doing it as middleware rather than
   per-`ModelAdmin`: with no admin login form served at all, the app's own
   `/login/` — Czech, and rate-limited by axes — is the only login form on the
   internet, so the two mitigations reinforce each other instead of leaving a
   second, unlimited door.

   **404 rather than a redirect to `/login/`** is the one judgement call. A
   redirect would be friendlier to a logged-out admin, and would also confirm to
   a scanner that the admin is here; an admin is one visit to the app away
   either way, so the trade goes to hiding it. The cost is a 404 that looks like
   a broken URL to someone who has not logged in yet, which the runbook states
   under [Before you point DNS at it](DEPLOYMENT.md#before-you-point-dns-at-it).

   The header link reads the same `User.has_admin_access` the gate does, so a
   visible „Administrace“ can never lead to that 404. What this does *not*
   replace: the source-IP block or a VPN, both of which keep the admin
   unreachable even from a stolen admin session's device — they are simply no
   longer the only thing standing between the superuser surface and the
   internet.
4. **No password reset and no email backend.** Unchanged from the LAN plan, and
   still workable — an admin resets, or `changepassword` over SSH — but the
   second admin account (runbook step 11) stops being a nicety.
5. **Everything under `static/` is now unauthenticated *to the internet*, not to
   the depot.** No new hole; `static/xlsx/` is already excluded from both
   `.gitignore` and `.dockerignore`. The consequence of getting it wrong is
   simply larger now.

## Files

**New**
- `Caddyfile` — the reverse proxy and TLS config; bind-mounted, not baked into
  the image
- `accounts/middleware.py` — the `/admin/` gate
- `templates/registration/lockout.html` — the Czech lockout page
- `DEPLOYMENT_PLAN_PUBLIC.md` — this file

**Changed**
- `config/settings.py` — the HTTPS block described above, plus the axes block,
  `AUTHENTICATION_BACKENDS` and `SILENCED_SYSTEM_CHECKS`
- `requirements.txt` — `django-axes==8.3.1` (no transitive dependencies)
- `accounts/tests.py` — `LoginRateLimitTests`, plus one case in
  `AdminIndexTests` for the section that stays out
- `docker-compose.prod.yml` — `proxy` service added; `web` loses `ports:`, gains
  `expose:` and `FORWARDED_ALLOW_IPS`; `caddy_data`/`caddy_config` volumes
- `.env.production.example` — `APP_BIND_IP`/`APP_PORT` out; `APP_DOMAIN`,
  `ACME_EMAIL` and the four HTTPS variables in; `CSRF_TRUSTED_ORIGINS` now
  filled rather than empty
- `.dockerignore` — excludes `Caddyfile`
- `DEPLOYMENT.md` — rewritten for the VPS; 16 steps, HTTPS verification, the
  pre-DNS security section, `down -v` replaced throughout
- `docs/configuration.md`, `CLAUDE.md`, `DEPLOYMENT_PLAN.md` — kept in step

**Deliberately unchanged**
- Every app, model, view and URL of this project's own — the only code added is
  the axes wiring, its lockout template and its tests
- `Dockerfile` — `collectstatic` and the `STATICFILES_BACKEND` `ENV` pairing is
  correct as it stands, and WhiteNoise serving `/static/` from inside the image
  behind a proxy needs nothing added
- `scripts/backup_db.sh` — the script is right; what changed is the advice about
  where the copies live

## Verification

Done on this machine, against the repo:

1. ✅ `docker compose --env-file <tmp> -f docker-compose.prod.yml config` parses
   and renders: `web` has `expose: 8000` and **no** published port,
   `FORWARDED_ALLOW_IPS: '*'`, `proxy` publishes 80, 443 and 443/udp, and the
   three volumes resolve as `materialinventory-prod_{db_data,caddy_data,caddy_config}`.
2. ✅ `manage.py check` — no issues.
3. ✅ The new settings really are inert by default. Read back from a bare
   checkout: `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`,
   `CSRF_COOKIE_SECURE`, `SECURE_HSTS_INCLUDE_SUBDOMAINS` and
   `SECURE_HSTS_PRELOAD` are all `False`, `SECURE_HSTS_SECONDS` is `0`. That is
   what `manage.py test` sees, and it is the same thing it saw before this
   change.
4. ✅ `manage.py check --deploy` with the production environment set
   (`DEBUG=False`, a real `SECRET_KEY`, `SECURE_SSL_REDIRECT=True`,
   `SECURE_COOKIES=True`, `SECURE_HSTS_SECONDS=3600`,
   `SECURE_HSTS_INCLUDE_SUBDOMAINS=True`, `BEHIND_PROXY=True`) reports
   **`1 issue (1 silenced)`** and the issue is `security.W021`. That is the count
   runbook step 12 tells you to expect, measured rather than assumed. Plain
   `manage.py check` is `no issues (1 silenced)`.
5. ✅ `manage.py test` — **280 tests, OK**, against the dev Postgres. That
   includes `LoginRateLimitTests` (five cases: below the limit, the lockout page
   at the limit, the lockout surviving the correct password, the lockout being
   per-username rather than per-address, and a successful login clearing the
   counter) and the 274 that existed before, none of which needed changing.

**Found while verifying, not planned.** Adding `axes` to `INSTALLED_APPS`
without running `migrate` does not fail at startup and does not fail on a page
load — it fails with a 500 on the first login *or logout*, because axes' signal
handlers hit `axes_accessattempt` before anything else touches it. `migrate` is
already in the runbook's update procedure, but the failure is worth knowing by
name, so it is now a troubleshooting row.

Everything else needs the server and is runbook steps 6–16: certificate issue,
the CSRF round trip over real HTTPS, and the reboot test.
