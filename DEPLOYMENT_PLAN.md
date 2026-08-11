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

**Network stability:** users connect over plain HTTP at `http://<server-lan-ip>:<APP_PORT>` — there's no domain name yet, so that IP *is* the address everyone bookmarks/memorizes. Two things have to hold for that to keep working:
- **The server's LAN IP must not change.** If it's on regular DHCP, the address can drift on lease renewal or reboot, silently breaking every saved URL with no obvious error. Get a DHCP reservation (or a static IP) for the server's MAC address from whoever manages the office network *before* handing out the URL — this is a prerequisite, not a nice-to-have.
- **The app must survive a server reboot on its own.** `docker-compose.prod.yml` already sets `restart: unless-stopped` on both services (see below), which tells the Docker daemon to bring the containers back up whenever it (re)starts. That only self-heals if the Docker daemon itself is enabled to start on boot — true by default on a standard install, but worth confirming explicitly (`systemctl is-enabled docker`) rather than assuming. Data isn't a concern either way: Postgres's named volume persists across container and host restarts, so a reboot means a short outage while things come back up, not data loss.

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
1. Check what's free: `sudo ss -tlnp | grep LISTEN` — confirm a port for `APP_PORT` (default suggestion `8080`) and confirm Docker is installed (`docker --version`); if not, install it first. Also confirm the Docker daemon starts on boot: `systemctl is-enabled docker` (if not `enabled`, run `sudo systemctl enable docker`) — this is what lets the containers recover automatically after a server reboot.
2. Get a DHCP reservation (or a static IP) for the server's MAC address from whoever manages the office network. Do this before step 9 — the LAN IP everyone will connect to must not change on lease renewal or reboot.
3. `git clone`/`pull` the repo.
4. `cp .env.production.example .env.production`, generate a real secret with `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`, fill in `SECRET_KEY`, `ALLOWED_HOSTS` (the server's reserved LAN IP), strong DB credentials, and `APP_PORT` if 8080 turns out to be taken.
5. `docker compose -f docker-compose.prod.yml up -d --build`
6. `docker compose -f docker-compose.prod.yml exec web python manage.py migrate`
7. Create real `seed_data/materials.csv`, `locations.csv`, `machines.csv` on the server (copy from the now-committed `.example.csv` versions, which already hold the real Petrokámen catalog) and run `python manage.py seed_data`.
8. `docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser` for the first real admin account.
9. Verify from a phone/laptop on the same LAN: `http://<server-ip>:<APP_PORT>`.
10. Set up the daily backup cron entry calling `scripts/backup_db.sh`.
11. Reboot the server (`sudo reboot`) and confirm both containers come back up on their own (`docker compose -f docker-compose.prod.yml ps`, both `Up`) and the app is still reachable at the same LAN IP, with no data loss.
12. If remote staff access is needed later, see "Remote access (off-LAN)" below — **not** domain + TLS/public exposure, which is deliberately out of scope unless the goal changes to public/customer-facing access.

## Remote access (off-LAN)

The app is LAN-only by design (see Network stability above). If a manager/admin needs to reach it from home or another site, the fix is a **VPN**, not public internet exposure — a VPN puts their device on the same private network as the server without opening the app up to the whole internet, needing a domain/TLS, or forwarding any ports.

**Recommended: [Tailscale](https://tailscale.com)** (hosted WireGuard mesh VPN):
1. Install it on the server: `curl -fsSL https://tailscale.com/install.sh | sh` then `tailscale up`. Authorize the node in the Tailscale admin console as a plain node — **not** a subnet router, so it only exposes the server itself to the tailnet, not the rest of the depot LAN or the other app/Postgres already on that server.
2. Note the server's assigned Tailscale IP (`100.x.y.z`) or MagicDNS name (`depot-server.<tailnet>.ts.net`).
3. Each remote staff member installs the Tailscale app on their own device and signs into the same tailnet (invited via the admin console — this is also where access is revoked per person, no config editing needed).
4. Add that Tailscale IP/hostname to `ALLOWED_HOSTS` in `.env.production`, alongside the depot LAN IP. That's the only app-facing change — restart `web` (`docker compose -f docker-compose.prod.yml up -d`) to pick it up.
5. Remote staff browse to `http://<tailscale-ip-or-magicdns>:<APP_PORT>` — same app, same login. No extra TLS needed since WireGuard traffic is already end-to-end encrypted.

If a dependency on Tailscale's (free-tier) coordination service is unwanted, **Headscale** (open-source, self-hosted Tailscale control server) is a drop-in alternative with the same client-side workflow.

Verify by connecting from a device genuinely off the depot LAN (e.g. phone on cellular data) and confirming the login page loads only while Tailscale shows "Connected" — and that revoking that device in the admin console immediately cuts its access.

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
4. Real deployment check happens on the server itself (runbook step 9) — loading the app from another device on the LAN.
5. Reboot check happens on the server itself (runbook step 11) — confirms the DHCP reservation and `restart: unless-stopped` actually hold up, not just look right on paper.
