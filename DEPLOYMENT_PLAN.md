# Deploy to the company server (LAN-only) — plan

**Every section of this plan has landed.** Sections 1-7 below record what was
built and why; the steps that can only be run on the server itself are in the
runbook, [DEPLOYMENT.md](DEPLOYMENT.md), which is what you actually follow to
deploy. This file is the reasoning behind that runbook, kept because most of it
is not recoverable from the files it describes.

What is left is the deployment itself — steps 1-14 of the runbook, on the
server. Nothing in the repo is blocking it.

## Context

The app currently only runs via the dev-oriented `docker-compose.yml` (Django `runserver`, `DEBUG=True`, DB port exposed to the host). We have a company server available and want the app running there for real use, reachable over the depot's LAN by IP (no domain/TLS for now — that can come later). The server already runs another Postgres instance and another web app, so the deployment must not collide with either.

While investigating, two concrete issues turned up that would block a naive deploy and need fixing as part of this work, not left to discover the hard way. **Both are now fixed** — kept here because the reasoning explains why the Dockerfile and `.gitignore` look the way they do.

1. ~~**`collectstatic` has never been run**~~ — **fixed**, but the original diagnosis was wrong in an instructive way. The symptom was real and reproduced: with `DEBUG=False`, `GET /login/` rendered `200` while `GET /static/img/logo.png` returned **404**, so every asset was broken in production. The stated cause — whitenoise's `CompressedManifestStaticFilesStorage` needing a manifest — was not, because **that backend was never active**. `config/settings.py` selected it via `STATICFILES_STORAGE`, a setting Django *removed in 5.1*; on Django 6.1 the line was silently ignored and the effective backend was plain `StaticFilesStorage`. The 404 was simply `DEBUG=False` turning off Django's own static serving with nothing collected into `STATIC_ROOT` for `WhiteNoiseMiddleware` to serve.

   Three things were needed, not one: move the backend into the `STORAGES` dict so the choice actually takes effect; add the `collectstatic` build step; and make sure the two agree about whether a manifest exists (see section 1). Manifest storage is now **opt-in per environment** rather than global — `manage.py test` forces `DEBUG=False`, and `DEBUG` is what makes hashed-URL lookup short-circuit, so a global default would have failed every template test until someone ran `collectstatic`.

2. ~~**`.gitignore` line 3 (`seed_data/`) blanket-excludes the whole directory**~~ — **fixed**. The blanket line is gone; the specific `seed_data/*.csv` + `!seed_data/*.example.csv` pair now works as intended and all three `*.example.csv` files (`materials`, `machines`, `users`) are committed, so a `git clone`/`pull` on the server brings the catalog templates along. (There were four until `locations.csv` went with the `Location` model.)

   The same class of bug was found a second time and also fixed: `.gitignore` ignored **`static/`**, the hand-maintained source directory in `STATICFILES_DIRS`, when the generated directory is `STATIC_ROOT` = **`staticfiles/`**. `templates/base.html` referenced `img/background.jpg`, which existed only on one developer's machine and had never been committed — a fresh clone or `docker build` produced a site with no background. The rule now ignores `staticfiles/`, and `static/img/background.jpg` is tracked.

## Approach

**Deployment model:** git-based (not rsync) — with the `.gitignore` fix landed, everything needed is trackable. The server does `git clone`/`git pull` from the existing GitHub remote; `.env.production` and the real `seed_data/*.csv` files (actual company data) are created directly on the server and never committed, same pattern already used for `.env` and `seed_data/*.csv` in dev.

**Port strategy:** since another Postgres and another web app already run on that server, avoid the conflict structurally instead of guessing free ports:
- Postgres is **not exposed to the host at all** in prod — the `web` container reaches it over the internal Compose network at `db:5432`, nothing external needs it. This sidesteps the other-Postgres conflict entirely without needing to know what port it uses.
- The app's own web port is configurable via an `APP_PORT` env var (default `8080`, clearly *not* 80/443/8000) so it's a one-line change in `.env.production` if that's also taken. First runbook step is checking what's actually free on the server.
- The prod compose file sets a top-level **`name: materialinventory-prod`**. Without it Compose derives the project name from the directory, and that name prefixes the container names *and* the `db_data` volume — so a differently-cloned path addresses a different volume by accident, and the dev `docker-compose.yml` in the same checkout addresses the *same* one. The `-prod` suffix is not cosmetic: runbook step 6 tells you to run `down -v` on a bad cluster, and under the bare name that command would take the development database with it.

**`.env.production` has to be passed twice, and that is not a mistake.** Compose reads two different things from two different places: `env_file:` on a service supplies variables *inside the container*, while `${APP_PORT}`-style **interpolation in the compose file itself** only ever reads the shell environment and the project's default `.env`. A `${APP_PORT}` in `ports:` therefore does **not** pick up `APP_PORT` from an `env_file:` entry — it silently expands to empty. So every command in the runbook carries `--env-file .env.production` *as well as* the service-level `env_file:`, and step 4 sets up a shell alias so nobody has to remember. This is the easiest thing here to get wrong, and it fails as a container bound to the wrong port or a `db` coming up with blank credentials, not as an error message.

**Network stability:** users connect over plain HTTP at `http://<server-lan-ip>:<APP_PORT>` — there's no domain name yet, so that IP *is* the address everyone bookmarks/memorizes. Two things have to hold for that to keep working:
- **The server's LAN IP must not change.** If it's on regular DHCP, the address can drift on lease renewal or reboot, silently breaking every saved URL with no obvious error. Get a DHCP reservation (or a static IP) for the server's MAC address from whoever manages the office network *before* handing out the URL — this is a prerequisite, not a nice-to-have.
- **The app must survive a server reboot on its own.** `docker-compose.prod.yml` sets `restart: unless-stopped` on both services (see below), which tells the Docker daemon to bring the containers back up whenever it (re)starts. That only self-heals if the Docker daemon itself is enabled to start on boot — true by default on a standard install, but worth confirming explicitly (`systemctl is-enabled docker`) rather than assuming. Data isn't a concern either way: Postgres's named volume persists across container and host restarts, so a reboot means a short outage while things come back up, not data loss.

**HTTPS is deliberately absent, and one check will complain about it.** `manage.py check --deploy` warns about `SECURE_HSTS_SECONDS` (W004), `SECURE_SSL_REDIRECT` (W008), `SESSION_COOKIE_SECURE` (W012) and `CSRF_COOKIE_SECURE` (W016) — verified against the built image: exactly those four, "4 issues (0 silenced)". On a plain-HTTP LAN deployment all four are correct as they stand — setting either `_SECURE` cookie flag would stop the session and CSRF cookies being sent at all, and nobody could log in. Run the check for the *other* findings (a weak `SECRET_KEY`, `DEBUG` left on) and expect those four. **CSRF needs no extra configuration either**: over plain HTTP Django compares the request's `Origin` against its own host, so the entry in `ALLOWED_HOSTS` is sufficient and `CSRF_TRUSTED_ORIGINS` — which is now wired to an env var of the same name, defaulting to empty — is left empty. That stops being true the moment anything terminates TLS in front of Django, a reverse proxy or a dev tunnel alike, which brings `CSRF_TRUSTED_ORIGINS` and `SECURE_PROXY_SSL_HEADER` with it.

### 1. `Dockerfile` — bake `collectstatic` into the image — **done**
`RUN python manage.py collectstatic --noinput` sits after `COPY . .`, before the `CMD`. It needs no DB and no real secrets (`config/settings.py` has `python-decouple` defaults for everything), so it is safe at build time.

The step it is paired with matters as much as the step itself: `ENV STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage` immediately above it. Because `ENV` persists into the running container, the backend that *writes* the manifest during the build and the backend that *reads* it at runtime are the same by construction — the failure mode where an image is built with plain storage and then started with manifest storage (500 on every page, no manifest to read) cannot happen. `config/settings.py` defaults `STATICFILES_BACKEND` to the plain backend so a bare checkout and `manage.py test` need no `collectstatic`.

Confirmed this doesn't affect dev: `docker-compose.yml` bind-mounts `.:/app`, shadowing the image's baked `staticfiles/`, **and** overrides `STATICFILES_BACKEND` back to plain storage in its `environment:` block — the mount alone isn't enough, because the test runner forces `DEBUG=False` and would then look for a manifest that the mount just hid.

A `.dockerignore` was added alongside, because `collectstatic` publishes whatever the build context leaves under `static/`. It keeps `.env`, `.venv/`, `.git/`, the real `seed_data/*.csv`, and a working spreadsheet at `static/xlsx/` out of the image — the last of which would otherwise have been served, unauthenticated, at `/static/xlsx/inventory-system-as-is.xlsx`.

### 2. `.gitignore` — drop the blanket `seed_data/` line — **done**
The blanket `seed_data/` line is removed and the `seed_data/*.example.csv` files are committed. The `static/` → `staticfiles/` correction described in Context #2 landed here too, together with `static/img/background.jpg` and an ignore for `static/xlsx/`.

One line still to add, when section 6 lands: **`backups/`**, so a dump of real company data can never be committed by a careless `git add -A` on the server.

### 3. `docker-compose.prod.yml` — **done**
Standalone (not an override), `db` + `web`, both `restart: unless-stopped` with
json-file log rotation. Postgres is not published to the host at all; the web
container reaches it at `db:5432`, which sidesteps the other Postgres on that
server without needing to know what port it uses.

Four things in the file are worth knowing before editing it:

- **The project name is `materialinventory-prod`, not `materialinventory`.** The
  plan originally said the latter, which is exactly the name Compose derives
  from the directory for the *dev* `docker-compose.yml` in the same checkout —
  they would have shared the `db_data` volume, and a `down -v` here (step 6 of
  the runbook tells you to run one) would have destroyed the development
  database.
- **`${DB_NAME:?...}` and friends, not bare `${DB_NAME}`.** Forgetting
  `--env-file .env.production` is the easiest mistake against this file, and the
  `:?` guard turns it into a named error instead of a database that comes up
  with blank credentials. Note this only fires when nothing else supplies the
  variable: Compose still reads a default `.env` if one exists in the directory,
  so a stray `.env` on the server would quietly hand the prod stack the dev
  credentials.
- **`APP_BIND_IP` publishes on one interface**, defaulting to `0.0.0.0` only so
  the file parses without it. Docker publishes ports through DNAT rules that sit
  ahead of `ufw`, so a host firewall does not contain a published port — binding
  to the reserved LAN IP is what makes "LAN-only" true rather than assumed.
- **`POSTGRES_INITDB_ARGS: "--locale-provider=icu --icu-locale=cs-CZ
  --encoding=UTF8"`** — see below.
- **One bind-mount, `./seed_data:/app/seed_data:ro`**, because `.dockerignore`
  keeps the real CSVs out of the image on purpose while `manage.py seed_data`
  reads them from the working directory. Without it the command prints
  `not found, skipping.` and exits 0, leaving the catalog silently empty.

**Czech collation, and why it is in this file rather than the runbook.** Postgres
fixes its sort order at `initdb` and cannot change it afterwards without a dump
and restore. Measured against `postgres:16-alpine`, the default cluster sorts
`Struska | Zemina | Štěrk` — byte order, with `Š` after `Z` — while the ICU
`cs-CZ` locale sorts `Struska | Štěrk | Zemina`. Both catalogs order by name
(`Material.Meta.ordering`, `Machine.Meta.ordering`), so this shows up in every
material dropdown on the Zpracování form and in the Materiál table. ICU
collations ship in the image, so this costs nothing but has to be right *before*
the first row is written — hence the check at runbook step 6, ahead of `migrate`.
The dev `docker-compose.yml` passes the same argument, but only a fresh volume
picks it up; an existing dev database keeps byte order until someone runs
`down -v`.

### 4. `.env.production.example` — **done**
`DEBUG=False`, an empty `SECRET_KEY` to fill per deploy, `ALLOWED_HOSTS`
seeded with a placeholder LAN IP plus `127.0.0.1,localhost` (so the app can be
curled from the server itself while debugging), an empty `CSRF_TRUSTED_ORIGINS`
with a note on when it stops being empty, `APP_BIND_IP`/`APP_PORT`,
`WEB_CONCURRENCY=3`, and DB credentials deliberately distinct from the dev
defaults.

`WEB_CONCURRENCY` is the one that is easy to skip: gunicorn defaults to **one**
worker, which serializes every request, so one manager opening Stroje over a
wide date range would block everyone else's page. Gunicorn reads the variable
natively, so the baked `CMD` needs no override.

Two things deliberately absent: `STATICFILES_BACKEND` (the `Dockerfile` `ENV`
owns it, and a second place to set it is a second place for it to drift), and
any `$` in the sample values — Compose interpolates this file, so a `$` in a
password would be read as a variable reference.

### 5. `config/settings.py` — `LOGGING` — **done**
One stdout `StreamHandler`, `django.request` at `ERROR` with
`propagate: False`. Without it an unhandled 500 in production is recorded
nowhere: Django's default routes those to `mail_admins` (no `ADMINS`, no mail
backend here) and filters its console handler to `require_debug_true`, while
gunicorn only sees an ordinary response come back. `ERROR` and not `WARNING`
because `django.request` logs every 4xx at `WARNING` and the suite asserts a
great many 403s from `role_required`.

### 6. `scripts/backup_db.sh` — **done**
`pg_dump | gzip` into `backups/`, with a 30-day `find -delete`, under
`set -euo pipefail`. The wrapper is the point: `pipefail` (without it a dump
that dies mid-stream still exits 0 through gzip, leaving a truncated archive
that looks like a backup), a `cd` to the repo (cron starts elsewhere), a
`-name` filter so the `-delete` cannot walk off into something else, and a size
check so a dump of an unreachable database fails loudly instead of reporting
success every night.

One deviation from the sketch: it does **not** `source .env.production`. That
would let the shell expand a `$` inside a password, so the script and Django
could disagree about the credentials; it greps out `DB_USER`/`DB_NAME` instead.
No password is needed — `pg_dump` runs inside the container over the local
socket, which the Postgres image trusts.

The script is committed with a `.gitattributes` rule (`*.sh text eol=lf`),
because this repo is edited on Windows and run on Linux, and a CRLF script dies
with `/usr/bin/env: bash
: No such file or directory`.

Still a manual step, on purpose: the cron entry is added on the server by hand
(runbook step 12). And the backups land on the same disk as the database, which
covers a deleted job and not a dead disk — copying them off-box is called out in
both the script header and the runbook.

### 7. `DEPLOYMENT.md` — **done**
The runbook is at [DEPLOYMENT.md](DEPLOYMENT.md): fourteen numbered install
steps, then "Deploying an update", "Routine maintenance", "Upgrading Postgres"
and a troubleshooting table. What it added beyond the sketch here:

- the **collation check at step 6**, placed between "first start" and "migrate"
  because that is the only window in which the fix is free;
- a **second admin account** at step 9, since there is no password-reset flow
  and no mail backend — a single admin who forgets their password leaves
  `manage.py changepassword` over SSH as the only way back in;
- **routine maintenance** the plan never mentioned: `clearsessions` (Django
  never prunes its session table), `docker image prune` (every `up --build`
  leaves a dangling image on a shared disk), and actually reading
  `backups/backup.log`, since a cron job that stops working is silent;
- an **Upgrading Postgres** section, because Dependabot watches Docker images
  and will open a PR bumping `postgres:16-alpine`. Merging it as an ordinary
  update takes the app down — Postgres refuses to start on a data directory
  written by a different major — so that PR is a planned dump/restore, not a
  merge.

## Remote access (off-LAN)

The app is LAN-only by design (see Network stability above). If a manager/admin needs to reach it from home or another site, the fix is a **VPN**, not public internet exposure — a VPN puts their device on the same private network as the server without opening the app up to the whole internet, needing a domain/TLS, or forwarding any ports.

**Recommended: [Tailscale](https://tailscale.com)** (hosted WireGuard mesh VPN):
1. Install it on the server: `curl -fsSL https://tailscale.com/install.sh | sh` then `tailscale up`. Authorize the node in the Tailscale admin console as a plain node — **not** a subnet router, so it only exposes the server itself to the tailnet, not the rest of the depot LAN or the other app/Postgres already on that server.
2. Note the server's assigned Tailscale IP (`100.x.y.z`) or MagicDNS name (`depot-server.<tailnet>.ts.net`).
3. Each remote staff member installs the Tailscale app on their own device and signs into the same tailnet (invited via the admin console — this is also where access is revoked per person, no config editing needed).
4. Add that Tailscale IP/hostname to `ALLOWED_HOSTS` in `.env.production`, alongside the depot LAN IP. That's the only app-facing change — restart `web` (`dcp up -d`) to pick it up. Add **both** the IP and the MagicDNS name: `ALLOWED_HOSTS` matches the host actually typed, so listing one and browsing to the other is a `DisallowedHost` 400 rather than a redirect.
5. Remote staff browse to `http://<tailscale-ip-or-magicdns>:<APP_PORT>` — same app, same login. No extra TLS needed since WireGuard traffic is already end-to-end encrypted.

If a dependency on Tailscale's (free-tier) coordination service is unwanted, **Headscale** (open-source, self-hosted Tailscale control server) is a drop-in alternative with the same client-side workflow.

Verify by connecting from a device genuinely off the depot LAN (e.g. phone on cellular data) and confirming the login page loads only while Tailscale shows "Connected" — and that revoking that device in the admin console immediately cuts its access.

## Critical files

All landed. Grouped by what they are, since the list is now a map of the
deployment rather than a to-do:

**Build and image**
- `Dockerfile` — `collectstatic` build step, paired with the `STATICFILES_BACKEND` `ENV`
- `.dockerignore` — keeps `.env`, `.git/`, the real `seed_data/*.csv` and `static/xlsx/` out of the image
- `config/settings.py` — static backend moved into `STORAGES`; `LOGGING` block added

**Running it**
- `docker-compose.prod.yml` — the production stack
- `.env.production.example` — the template; the filled-in file lives only on the server
- `scripts/backup_db.sh` — nightly dump, installed as a cron entry by hand

**Guard rails that are easy to overlook**
- `.gitignore` — no blanket `seed_data/`; ignores `staticfiles/` not `static/`; plus `.env.production` (the existing `.env` line does **not** match it) and `backups/`
- `.gitattributes` — `*.sh text eol=lf`, so a Windows checkout cannot produce a CRLF script that Linux refuses to run
- `docker-compose.yml` — the dev file also got `POSTGRES_INITDB_ARGS`, so a fresh dev database sorts Czech like production

**Documentation**
- `DEPLOYMENT.md` — the runbook
- `docs/configuration.md` — deployment-only variables, database collation, logging

**Found while verifying, not planned**
- `materials/management/commands/seed_data.py` — a short CSV row (`Bagr,10` under a three-column header) crashed the command with an `AttributeError`, because `csv.DictReader` fills unreached columns with `None`. The real `machines.csv` has exactly that shape, so runbook step 8 would have failed on the server. Fixed, with a missing-required-column `CommandError` alongside it and four tests.

## Verification

Run on 2026-09-09 against the built production image, with the stack brought up
under its own Compose project name so the development database was never
touched. Everything here is reproducible from the repo; only the last three
items need the server.

**The image and its static files**

1. ✅ `docker build` completes — `135 static files copied to '/app/staticfiles', 397 post-processed`. The *post-processed* count is the part that matters: it proves manifest storage was actually active, rather than the build quietly succeeding with plain storage.
2. ✅ `GET /login/` → **200**, and the HTML carries `/static/css/app.20c9ead09c93.css` and `/static/img/logo.bef90a29a416.png`. Hashed names mean the manifest is being *read* at runtime, not merely written at build time. `GET /static/css/app.css` → 200, `GET /` → 302 to login.

**The compose file**

3. ✅ `docker compose --env-file .env.production -f docker-compose.prod.yml config` parses; `up -d --build` takes `db` to `healthy` and then starts `web`.
4. ✅ `WEB_CONCURRENCY=3` reaches gunicorn — three `Booting worker` lines in the log.

**Collation — measured, not assumed**

5. ✅ A default `postgres:16-alpine` cluster sorts `Olse | Olše | Struska | Zemina | Štěrk`: byte order, `Š` after `Z`. With `POSTGRES_INITDB_ARGS` the same cluster reports `datlocprovider = i`, `daticulocale = cs-CZ` and sorts `Olše | Struska | Štěrk | Zemina` — read back through `Material.Meta.ordering` in the running app, not just in psql.

**Logging**

6. ✅ `django.request` resolves to level `ERROR`, one `StreamHandler`, `propagate=False`, and a record written to it appears on stdout. Gunicorn's own stdout is what `docker compose logs web` shows, so the chain holds; forcing a *real* 500 through the running server is left as an on-server check.

**Configuration**

7. ✅ `manage.py check --deploy` → exactly `W004`, `W008`, `W012`, `W016`, "4 issues (0 silenced)". Nothing about `SECRET_KEY` or `DEBUG`, which is what proves `.env.production` was picked up.

**`seed_data` — where a real bug turned up**

8. ✅ The first run **crashed**: `AttributeError: 'NoneType' object has no attribute 'strip'`, on the depot's actual `machines.csv`. `csv.DictReader` fills a short row's unreached columns with `None`, and rows like `Bagr,10` under a three-column header are exactly what the real file contains. Runbook step 8 would have failed on the server, on real data, with the catalog half-loaded. Fixed (`_cell`), plus a `CommandError` for a genuinely wrong header, plus four tests. Re-run: `Materials: 13 created. Machines: 7 created. Created 3 user(s).`

**Suite and lint**

9. ✅ `manage.py test` → **274 tests, OK** (270 before, plus four for the seeder fix). `ruff check .` and `ruff format --check .` are clean **on the host**. Ruff run *inside* the container instead reports 30 × `EXE002` ("file is executable but no shebang") — an artifact of Docker copying from a Windows build context, where every file arrives mode 0755. CI checks out on Linux and never sees it, so don't chase it.

Still outstanding, and only checkable on the server:

10. LAN check — runbook step 11, loading the app from another device.
11. Restore rehearsal — runbook step 13, the step that turns a backup script into a backup.
12. Reboot check — runbook step 14, which is what actually tests the DHCP reservation and `restart: unless-stopped`.
