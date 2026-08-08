# Deploy to the company server (LAN-only) — plan

Not yet implemented. Saved for when we're ready to act on it.

## Context

The app currently only runs via the dev-oriented `docker-compose.yml` (Django `runserver`, `DEBUG=True`, DB port exposed to the host). We have a company server available and want the app running there for real use, reachable over the depot's LAN by IP (no domain/TLS for now — that can come later). The server already runs another Postgres instance and another web app, so the deployment must not collide with either.

While investigating, two concrete issues turned up that would block a naive deploy and need fixing as part of this work, not left to discover the hard way:

1. **`collectstatic` has never been run**, and whitenoise's `CompressedManifestStaticFilesStorage` needs it. Verified directly: with `DEBUG=False` (the real production condition), `GET /login/` renders `200` with `<img src="/static/img/logo.png">` in the HTML, but `GET /static/img/logo.png` itself returns **404** — the logo (and any other static asset) would be broken on every page in production. In dev this is invisible because `runserver` + `DEBUG=True` serves static files through Django's finders directly, bypassing the manifest.
2. **`.gitignore` line 3 (`seed_data/`) blanket-excludes the whole directory**, which silently defeats the more specific `seed_data/*.csv` / `!seed_data/*.example.csv` rules added later — git cannot un-ignore a file inside an already-ignored directory. Confirmed: `git ls-files seed_data/` returns nothing. The example CSVs (the real Petrokámen catalog) were never actually committed, despite `CLAUDE.md` documenting them as the seeding starting point. This matters for deployment because it determines whether a `git clone`/`pull` on the server actually brings the catalog templates along.

Both are one-line-ish fixes, folded into this plan rather than done separately.

## Approach

**Deployment model:** git-based (not rsync) — once the `.gitignore` fix lands, everything needed is trackable. The server does `git clone`/`git pull` from the existing GitHub remote; `.env.production` and the real `seed_data/*.csv` files (actual company data) are created directly on the server and never committed, same pattern already used for `.env` and `seed_data/*.csv` in dev.

**Port strategy:** since another Postgres and another web app already run on that server, avoid the conflict structurally instead of guessing free ports:
- Postgres is **not exposed to the host at all** in prod — the `web` container reaches it over the internal Compose network at `db:5432`, nothing external needs it. This sidesteps the other-Postgres conflict entirely without needing to know what port it uses.
- The app's own web port is configurable via an `APP_PORT` env var (default `8080`, clearly *not* 80/443/8000) so it's a one-line change in `.env.production` if that's also taken. First runbook step is checking what's actually free on the server.

### 1. `Dockerfile` — bake `collectstatic` into the image
Add `RUN python manage.py collectstatic --noinput` after `COPY . .`, before the `CMD`. This needs no DB and no real secrets (`config/settings.py` already has safe defaults for everything via `python-decouple`), so it's safe at build time. Confirmed this doesn't affect dev: `docker-compose.yml` bind-mounts `.:/app`, which shadows the image's baked `staticfiles/` anyway, but dev never depends on it since `runserver`+`DEBUG=True` bypasses the manifest.

### 2. `.gitignore` — drop the blanket `seed_data/` line
Remove line 3 (`seed_data/`). The existing `seed_data/*.csv` + `!seed_data/*.example.csv` pair (lines 13-14) already express the intended rule correctly on their own. After this, `git add` the four `seed_data/*.example.csv` files so they're actually committed.

### 3. `docker-compose.prod.yml` — new file
Standalone (not an override) for clarity:
- `db`: `postgres:16-alpine`, named volume, healthcheck (reuse the pattern from `docker-compose.yml`), **no `ports:` mapping**, `restart: unless-stopped`.
- `web`: `build: .` (uses the Dockerfile's baked `collectstatic` + its existing `gunicorn config.wsgi:application --bind 0.0.0.0:8000` CMD — no command override needed), `env_file: .env.production`, `environment: DB_HOST: db`, `ports: ["${APP_PORT:-8080}:8000"]`, **no bind-mount** (prod runs the built image as-is), `restart: unless-stopped`, `depends_on: db: condition: service_healthy`.

### 4. `.env.production.example` — new file
Mirrors `.env.example`'s shape: `DEBUG=False`, `SECRET_KEY=` (placeholder, generated per-deploy — see runbook), `ALLOWED_HOSTS=` (placeholder for the server's LAN IP), `APP_PORT=8080`, distinct/strong `DB_NAME`/`DB_USER`/`DB_PASSWORD` placeholders (not the dev defaults), `DB_HOST=db`, `DB_PORT=5432`.

### 5. `scripts/backup_db.sh` — new file
Small script: `docker compose -f docker-compose.prod.yml exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip > backups/materialinventory_$(date +%F_%H%M).sql.gz`, plus a `find backups/ -mtime +30 -delete` retention line. Documented as a daily cron entry in the runbook, not wired up automatically (added to the server's crontab by hand).

### 6. `DEPLOYMENT.md` — new runbook at repo root
Ordered, copy-pasteable steps to run on the server itself:
1. Check what's free: `sudo ss -tlnp | grep LISTEN` — confirm a port for `APP_PORT` (default suggestion `8080`) and confirm Docker is installed (`docker --version`); if not, install it first.
2. `git clone`/`pull` the repo.
3. `cp .env.production.example .env.production`, generate a real secret with `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`, fill in `SECRET_KEY`, `ALLOWED_HOSTS` (the server's `hostname -I` output), strong DB credentials, and `APP_PORT` if 8080 turns out to be taken.
4. `docker compose -f docker-compose.prod.yml up -d --build`
5. `docker compose -f docker-compose.prod.yml exec web python manage.py migrate`
6. Create real `seed_data/materials.csv`, `locations.csv`, `machines.csv` on the server (copy from the now-committed `.example.csv` versions, which already hold the real Petrokámen catalog) and run `python manage.py seed_data`.
7. `docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser` for the first real admin account.
8. Verify from a phone/laptop on the same LAN: `http://<server-ip>:<APP_PORT>`.
9. Set up the daily backup cron entry calling `scripts/backup_db.sh`.
10. Later, separate pass: domain + TLS (Caddy/nginx) if/when the app needs to be reachable off the LAN.

## Critical files
- `Dockerfile` — add `collectstatic` build step
- `.gitignore` — remove blanket `seed_data/` line
- `docker-compose.prod.yml` — new
- `.env.production.example` — new
- `scripts/backup_db.sh` — new
- `DEPLOYMENT.md` — new

## Verification (once we implement this)
1. `docker build -t materialinventory-test .` locally and confirm it completes (proves `collectstatic` succeeds at build time with no `.env` present).
2. `git status` after the `.gitignore` fix — confirm the four `seed_data/*.example.csv` files now show as addable/tracked.
3. `python manage.py test` — confirm the existing suite still passes (no application code changes, only Dockerfile/compose/docs, so this is a sanity check not a real risk).
4. Real deployment check happens on the server itself (runbook step 8) — loading the app from another device on the LAN.
