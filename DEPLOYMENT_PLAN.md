# Deploy to the company server (LAN-only) — plan

Not yet implemented, **except** for the two blockers in "Context" below and the
`Dockerfile`/`.gitignore` steps that fix them (sections 1 and 2) — those have
landed. Sections 3-7 are still to do. Saved for when we're ready to act on it.

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
- The prod compose file sets a top-level **`name: materialinventory`**. Without it Compose derives the project name from the directory, and that name prefixes the container names *and* the `db_data` volume — so a differently-cloned path, or a stray `docker compose up` from this repo's *dev* file, addresses a different (or worse, the same) volume by accident. Naming it explicitly makes the isolation independent of where the repo happens to sit.

**`.env.production` has to be passed twice, and that is not a mistake.** Compose reads two different things from two different places: `env_file:` on a service supplies variables *inside the container*, while `${APP_PORT}`-style **interpolation in the compose file itself** only ever reads the shell environment and the project's default `.env`. A `${APP_PORT}` in `ports:` therefore does **not** pick up `APP_PORT` from an `env_file:` entry — it silently expands to empty. So every command in the runbook carries `--env-file .env.production` *as well as* the service-level `env_file:`, and step 4 sets up a shell alias so nobody has to remember. This is the easiest thing here to get wrong, and it fails as a container bound to the wrong port or a `db` coming up with blank credentials, not as an error message.

**Network stability:** users connect over plain HTTP at `http://<server-lan-ip>:<APP_PORT>` — there's no domain name yet, so that IP *is* the address everyone bookmarks/memorizes. Two things have to hold for that to keep working:
- **The server's LAN IP must not change.** If it's on regular DHCP, the address can drift on lease renewal or reboot, silently breaking every saved URL with no obvious error. Get a DHCP reservation (or a static IP) for the server's MAC address from whoever manages the office network *before* handing out the URL — this is a prerequisite, not a nice-to-have.
- **The app must survive a server reboot on its own.** `docker-compose.prod.yml` sets `restart: unless-stopped` on both services (see below), which tells the Docker daemon to bring the containers back up whenever it (re)starts. That only self-heals if the Docker daemon itself is enabled to start on boot — true by default on a standard install, but worth confirming explicitly (`systemctl is-enabled docker`) rather than assuming. Data isn't a concern either way: Postgres's named volume persists across container and host restarts, so a reboot means a short outage while things come back up, not data loss.

**HTTPS is deliberately absent, and one check will complain about it.** `manage.py check --deploy` warns about `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`. On a plain-HTTP LAN deployment all four are correct as they stand — setting either `_SECURE` cookie flag would stop the session and CSRF cookies being sent at all, and nobody could log in. Run the check for the *other* findings (a weak `SECRET_KEY`, `DEBUG` left on) and expect those four. **CSRF needs no extra configuration either**: over plain HTTP Django compares the request's `Origin` against its own host, so the entry in `ALLOWED_HOSTS` is sufficient and `CSRF_TRUSTED_ORIGINS` stays unset. That stops being true the moment a TLS-terminating reverse proxy goes in front, which brings `CSRF_TRUSTED_ORIGINS` and `SECURE_PROXY_SSL_HEADER` with it.

### 1. `Dockerfile` — bake `collectstatic` into the image — **done**
`RUN python manage.py collectstatic --noinput` sits after `COPY . .`, before the `CMD`. It needs no DB and no real secrets (`config/settings.py` has `python-decouple` defaults for everything), so it is safe at build time.

The step it is paired with matters as much as the step itself: `ENV STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage` immediately above it. Because `ENV` persists into the running container, the backend that *writes* the manifest during the build and the backend that *reads* it at runtime are the same by construction — the failure mode where an image is built with plain storage and then started with manifest storage (500 on every page, no manifest to read) cannot happen. `config/settings.py` defaults `STATICFILES_BACKEND` to the plain backend so a bare checkout and `manage.py test` need no `collectstatic`.

Confirmed this doesn't affect dev: `docker-compose.yml` bind-mounts `.:/app`, shadowing the image's baked `staticfiles/`, **and** overrides `STATICFILES_BACKEND` back to plain storage in its `environment:` block — the mount alone isn't enough, because the test runner forces `DEBUG=False` and would then look for a manifest that the mount just hid.

A `.dockerignore` was added alongside, because `collectstatic` publishes whatever the build context leaves under `static/`. It keeps `.env`, `.venv/`, `.git/`, the real `seed_data/*.csv`, and a working spreadsheet at `static/xlsx/` out of the image — the last of which would otherwise have been served, unauthenticated, at `/static/xlsx/inventory-system-as-is.xlsx`.

### 2. `.gitignore` — drop the blanket `seed_data/` line — **done**
The blanket `seed_data/` line is removed and the `seed_data/*.example.csv` files are committed. The `static/` → `staticfiles/` correction described in Context #2 landed here too, together with `static/img/background.jpg` and an ignore for `static/xlsx/`.

One line still to add, when section 6 lands: **`backups/`**, so a dump of real company data can never be committed by a careless `git add -A` on the server.

### 3. `docker-compose.prod.yml` — new file
Standalone (not an override) for clarity, with a top-level `name: materialinventory`:
- `db`: `postgres:16-alpine`, named volume, healthcheck (reuse the pattern from `docker-compose.yml`), **no `ports:` mapping**, `restart: unless-stopped`. `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` come from `${DB_NAME}`/`${DB_USER}`/`${DB_PASSWORD}` interpolation — which is why `--env-file .env.production` is not optional (see Approach). Write them **without `:-` defaults**, unlike the dev file: a missing env file should fail loudly rather than quietly stand up a database on the dev credentials.
- `web`: `build: .` (uses the Dockerfile's baked `collectstatic` + its existing `gunicorn config.wsgi:application --bind 0.0.0.0:8000` CMD — no command override needed), `env_file: .env.production`, `environment: DB_HOST: db`, `ports: ["${APP_PORT:-8080}:8000"]`, `restart: unless-stopped`, `depends_on: db: condition: service_healthy`.
- **One bind-mount on `web`, and only one:** `./seed_data:/app/seed_data:ro`. Prod otherwise runs the built image as-is, but `.dockerignore` excludes `seed_data/*.csv` from the image *on purpose* (real company data must not be baked into a layer), while `manage.py seed_data` defaults to `seed_data/materials.csv` and friends. Without the mount, runbook step 7 does not fail — `_read_csv` prints `... not found, skipping.` and the command exits 0 — so the catalog silently stays empty and the first worker to open Zpracování finds no materials to pick. Read-only, since the command only ever reads them.
- **Log rotation:** `logging: driver: json-file` with `max-size: "10m"`, `max-file: "3"` on both services. The default json-file driver grows without bound, and on a shared company server the thing that eventually fills the disk should not be this app's log.

### 4. `.env.production.example` — new file
Mirrors `.env.example`'s shape: `DEBUG=False`, `SECRET_KEY=` (placeholder, generated per-deploy — see runbook), `ALLOWED_HOSTS=` (placeholder for the server's LAN IP), `APP_PORT=8080`, distinct/strong `DB_NAME`/`DB_USER`/`DB_PASSWORD` placeholders (not the dev defaults), `DB_HOST=db`, `DB_PORT=5432`.

Plus `WEB_CONCURRENCY=3`. Gunicorn reads that variable natively as its worker count and **defaults to 1** — a single synchronous worker serializes every request, so one manager opening Stroje over a wide date range blocks everyone else's page until it renders. Three is ample for a depot, and going through the env var means the baked `CMD` stays as it is, which is the whole reason not to override `command:` here. (`GUNICORN_CMD_ARGS=--access-logfile -` is the same trick if request logging is ever wanted; leave it out by default.)

Deliberately **not** in this file: `STATICFILES_BACKEND`. The `Dockerfile` `ENV` already covers it, and there should be exactly one place that decides it.

### 5. `config/settings.py` — a `LOGGING` block, so production errors are visible at all
There is none today, which is invisible in dev and a hole in prod. Django's default config routes `django.request` errors to `mail_admins` — filtered to `DEBUG=False`, but `ADMINS` is empty and no mail backend is configured — while its console handler carries `require_debug_true`. Net effect on the server: **an unhandled 500 renders the plain error page and writes nothing anywhere.** `docker compose logs web` shows only the gunicorn access line, because as far as gunicorn is concerned Django returned a perfectly ordinary response. Chasing a report from a depot worker would start with no traceback.

Add a minimal block: one `StreamHandler` to stdout, and a `django.request` logger at level **`ERROR`** with `propagate: False`. `ERROR` specifically, not `WARNING` — `django.request` logs 4xx at `WARNING`, and the suite is full of tests asserting the 403s from `role_required`, which would then spray warnings through the test output. Docker captures stdout, so the rotation from section 3 already covers the volume.

### 6. `scripts/backup_db.sh` — new file
The `pg_dump` line is the easy part; the wrapper is what makes it survive a crontab:

```sh
#!/usr/bin/env bash
set -euo pipefail          # pipefail matters: without it a pg_dump that dies
                           # mid-stream still exits 0 through gzip, leaving a
                           # truncated archive that looks like a backup
cd "$(dirname "$0")/.."    # cron's cwd is not the repo
set -a; . ./.env.production; set +a   # $DB_USER/$DB_NAME are not in cron's env
mkdir -p backups
docker compose --env-file .env.production -f docker-compose.prod.yml \
  exec -T db pg_dump -U "$DB_USER" "$DB_NAME" \
  | gzip > "backups/materialinventory_$(date +%F_%H%M).sql.gz"
find backups/ -name '*.sql.gz' -mtime +30 -delete
```

Three details worth keeping: `pipefail`, per the comment; the `-name` filter on the `find`, so a `-delete` can never walk off into something that isn't a backup; and the `cd`, because cron starts elsewhere and both the compose file and `backups/` are repo-relative. Documented as a daily cron entry in the runbook and added to the server's crontab by hand, not wired up automatically.

**A backup nobody has restored is not a backup**, so the runbook rehearses a restore (step 12) rather than leaving it as an exercise for the day it matters.

### 7. `DEPLOYMENT.md` — new runbook at repo root
Ordered, copy-pasteable steps to run on the server itself:
1. Check what's free: `sudo ss -tlnp | grep LISTEN` — confirm a port for `APP_PORT` (default suggestion `8080`) and confirm Docker is installed (`docker --version`); if not, install it first. Also confirm the Docker daemon starts on boot: `systemctl is-enabled docker` (if not `enabled`, run `sudo systemctl enable docker`) — this is what lets the containers recover automatically after a server reboot.
2. Get a DHCP reservation (or a static IP) for the server's MAC address from whoever manages the office network. Do this before step 10 — the LAN IP everyone will connect to must not change on lease renewal or reboot.
3. `git clone`/`pull` the repo.
4. `cp .env.production.example .env.production`, generate a real secret with `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`, fill in `SECRET_KEY`, `ALLOWED_HOSTS` (the server's reserved LAN IP), strong DB credentials, and `APP_PORT` if 8080 turns out to be taken. Then set up the alias every later step uses — and say plainly, right there, that dropping the `--env-file` is what silently breaks the port and the DB credentials:
   `alias dcp='docker compose --env-file .env.production -f docker-compose.prod.yml'`
5. `dcp up -d --build`
6. `dcp exec web python manage.py migrate`
7. Create the real `seed_data/materials.csv`, `machines.csv` and `users.csv` on the server from the committed `.example.csv` files. The three are not equivalent: **`materials` and `machines` already carry the real catalog** (the depot's SKUs, and machines with their Kč rates), so a `cp` plus a spot-check of the rates is enough — while **`users.example.csv` is placeholder rows** (`worker.one`, `manager.one`, `admin.one`) and has to be rewritten with actual staff before it is any use. Then `dcp exec web python manage.py seed_data`. Read the output: `Materials: N created` is success, while `seed_data/materials.csv not found, skipping.` means the read-only `./seed_data` mount from section 3 is missing. The users pass prints a random temporary password per new account — capture them and hand them out securely, they are not shown again.
8. `dcp exec web python manage.py createsuperuser` for the first real admin account, then set its `role` to `ADMIN` in the admin. `createsuperuser` leaves `role` at the `WORKER` default, and while both `role_required` and `is_manager_or_admin` bypass that for superusers, an account whose displayed role contradicts its access is a trap for whoever looks next.
9. `dcp exec web python manage.py check --deploy` — expect exactly the four TLS warnings described in Approach, and nothing else. Anything about `SECRET_KEY` or `DEBUG` means `.env.production` did not take.
10. Verify from a phone/laptop on the same LAN: `http://<server-ip>:<APP_PORT>`. Load the login page, sign in, submit one throwaway Zpracování job, and confirm the logo and background render — a manifest problem shows up here as a 500, not as a missing image.
11. Set up the daily backup cron entry calling `scripts/backup_db.sh`, then run it once by hand and confirm a non-empty `.gz` lands in `backups/`.
12. Rehearse a restore once, before anyone depends on it: `gunzip -c backups/<file>.sql.gz | dcp exec -T db psql -U "$DB_USER" -d <scratch-db>` into a throwaway database, and check the row counts match. Write down whichever command actually worked — that note is the real artifact at 8am on a bad day.
13. Reboot the server (`sudo reboot`) and confirm both containers come back on their own (`dcp ps`, both `Up`), the app is reachable at the same LAN IP, and the throwaway job from step 10 is still there.
14. If remote staff access is needed later, see "Remote access (off-LAN)" below — **not** domain + TLS/public exposure, which is deliberately out of scope unless the goal changes to public/customer-facing access.

The runbook also carries a short **"Deploying an update"** section — the part that runs dozens of times, where the install above runs once:

```sh
git pull
dcp up -d --build          # rebuild: collectstatic runs in the image, not at boot
dcp exec web python manage.py migrate
```

with two notes attached: run `migrate` even when the pull looks harmless (CI's `makemigrations --check` guarantees the migration file exists, nothing guarantees it has been applied to this database), and take a backup first whenever the pull contains one.

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
- ~~`Dockerfile` — add `collectstatic` build step~~ — done
- ~~`config/settings.py` — move the static backend from the removed `STATICFILES_STORAGE` into `STORAGES`~~ — done
- ~~`.gitignore` — remove blanket `seed_data/` line; ignore `staticfiles/` not `static/`~~ — done
- ~~`.dockerignore` — new~~ — done
- `docker-compose.prod.yml` — new
- `.env.production.example` — new — no `STATICFILES_BACKEND` (the `Dockerfile` `ENV` owns it); does carry `WEB_CONCURRENCY`
- `config/settings.py` — add the `LOGGING` block (section 5)
- `scripts/backup_db.sh` — new
- `.gitignore` — add `backups/` alongside it
- `DEPLOYMENT.md` — new

## Verification

Steps 1-4 cover the work already done and were run when sections 1-2 landed. **The numbers below are from that run and have drifted since** — the suite is 270 tests today, and the static-file counts moved when Locations were removed — so re-run all four before deploying rather than trusting the record:

1. ✅ `docker build -t materialinventory-test .` completes; the build log showed `200 static files copied to '/app/staticfiles', 574 post-processed`. The post-processing count is the part that matters: it proves manifest storage was actually active, rather than the build quietly succeeding with plain storage.
2. ✅ `git status` — the `seed_data/*.example.csv` files are tracked, and so is `static/img/background.jpg`.
3. ✅ `python manage.py test` — `OK` (171 tests at the time, 270 now).
4. ✅ End-to-end static check against the built image, the thing the original 404 report was about. Run it with `DEBUG=False` on the compose network and confirm:
   - `GET /static/img/logo.png` → **200** (was 404)
   - `GET /static/img/background.jpg` → **200**
   - `GET /login/` → **200**, with `img/logo.<hash>.png` and `img/background.<hash>.jpg` in the HTML — hashed names confirm the manifest is being read at runtime, not just written at build time
   - `GET /static/xlsx/inventory-system-as-is.xlsx` → **404**, and `/app/.env` absent from the image

Still outstanding, and only checkable on the server:

5. Real deployment check (runbook step 10) — loading the app from another device on the LAN.
6. Restore rehearsal (runbook step 12) — the step that turns a backup script into a backup.
7. Reboot check (runbook step 13) — confirms the DHCP reservation and `restart: unless-stopped` actually hold up, not just look right on paper.
