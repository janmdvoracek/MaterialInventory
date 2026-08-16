# Deploy to the company server (LAN-only) — plan

Not yet implemented, **except** for the two blockers in "Context" below and the
`Dockerfile`/`.gitignore` steps that fix them (sections 1 and 2) — those have
landed. Sections 3-6 are still to do. Saved for when we're ready to act on it.

## Context

The app currently only runs via the dev-oriented `docker-compose.yml` (Django `runserver`, `DEBUG=True`, DB port exposed to the host). We have a company server available and want the app running there for real use, reachable over the depot's LAN by IP (no domain/TLS for now — that can come later). The server already runs another Postgres instance and another web app, so the deployment must not collide with either.

While investigating, two concrete issues turned up that would block a naive deploy and need fixing as part of this work, not left to discover the hard way. **Both are now fixed** — kept here because the reasoning explains why the Dockerfile and `.gitignore` look the way they do.

1. ~~**`collectstatic` has never been run**~~ — **fixed**, but the original diagnosis was wrong in an instructive way. The symptom was real and reproduced: with `DEBUG=False`, `GET /login/` rendered `200` while `GET /static/img/logo.png` returned **404**, so every asset was broken in production. The stated cause — whitenoise's `CompressedManifestStaticFilesStorage` needing a manifest — was not, because **that backend was never active**. `config/settings.py` selected it via `STATICFILES_STORAGE`, a setting Django *removed in 5.1*; on Django 6.1 the line was silently ignored and the effective backend was plain `StaticFilesStorage`. The 404 was simply `DEBUG=False` turning off Django's own static serving with nothing collected into `STATIC_ROOT` for `WhiteNoiseMiddleware` to serve.

   Three things were needed, not one: move the backend into the `STORAGES` dict so the choice actually takes effect; add the `collectstatic` build step; and make sure the two agree about whether a manifest exists (see section 1). Manifest storage is now **opt-in per environment** rather than global — `manage.py test` forces `DEBUG=False`, and `DEBUG` is what makes hashed-URL lookup short-circuit, so a global default would have failed every template test until someone ran `collectstatic`.

2. ~~**`.gitignore` line 3 (`seed_data/`) blanket-excludes the whole directory**~~ — **fixed**. The blanket line is gone; the specific `seed_data/*.csv` + `!seed_data/*.example.csv` pair now works as intended and all four `*.example.csv` files are committed, so a `git clone`/`pull` on the server brings the catalog templates along.

   The same class of bug was found a second time and also fixed: `.gitignore` ignored **`static/`**, the hand-maintained source directory in `STATICFILES_DIRS`, when the generated directory is `STATIC_ROOT` = **`staticfiles/`**. `templates/base.html` referenced `img/background.jpg`, which existed only on one developer's machine and had never been committed — a fresh clone or `docker build` produced a site with no background. The rule now ignores `staticfiles/`, and `static/img/background.jpg` is tracked.

## Approach

**Deployment model:** git-based (not rsync) — once the `.gitignore` fix lands, everything needed is trackable. The server does `git clone`/`git pull` from the existing GitHub remote; `.env.production` and the real `seed_data/*.csv` files (actual company data) are created directly on the server and never committed, same pattern already used for `.env` and `seed_data/*.csv` in dev.

**Port strategy:** since another Postgres and another web app already run on that server, avoid the conflict structurally instead of guessing free ports:
- Postgres is **not exposed to the host at all** in prod — the `web` container reaches it over the internal Compose network at `db:5432`, nothing external needs it. This sidesteps the other-Postgres conflict entirely without needing to know what port it uses.
- The app's own web port is configurable via an `APP_PORT` env var (default `8080`, clearly *not* 80/443/8000) so it's a one-line change in `.env.production` if that's also taken. First runbook step is checking what's actually free on the server.

**Network stability:** users connect over plain HTTP at `http://<server-lan-ip>:<APP_PORT>` — there's no domain name yet, so that IP *is* the address everyone bookmarks/memorizes. Two things have to hold for that to keep working:
- **The server's LAN IP must not change.** If it's on regular DHCP, the address can drift on lease renewal or reboot, silently breaking every saved URL with no obvious error. Get a DHCP reservation (or a static IP) for the server's MAC address from whoever manages the office network *before* handing out the URL — this is a prerequisite, not a nice-to-have.
- **The app must survive a server reboot on its own.** `docker-compose.prod.yml` already sets `restart: unless-stopped` on both services (see below), which tells the Docker daemon to bring the containers back up whenever it (re)starts. That only self-heals if the Docker daemon itself is enabled to start on boot — true by default on a standard install, but worth confirming explicitly (`systemctl is-enabled docker`) rather than assuming. Data isn't a concern either way: Postgres's named volume persists across container and host restarts, so a reboot means a short outage while things come back up, not data loss.

### 1. `Dockerfile` — bake `collectstatic` into the image — **done**
`RUN python manage.py collectstatic --noinput` sits after `COPY . .`, before the `CMD`. It needs no DB and no real secrets (`config/settings.py` has `python-decouple` defaults for everything), so it is safe at build time.

The step it is paired with matters as much as the step itself: `ENV STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage` immediately above it. Because `ENV` persists into the running container, the backend that *writes* the manifest during the build and the backend that *reads* it at runtime are the same by construction — the failure mode where an image is built with plain storage and then started with manifest storage (500 on every page, no manifest to read) cannot happen. `config/settings.py` defaults `STATICFILES_BACKEND` to the plain backend so a bare checkout and `manage.py test` need no `collectstatic`.

Confirmed this doesn't affect dev: `docker-compose.yml` bind-mounts `.:/app`, shadowing the image's baked `staticfiles/`, and `runserver` + `DEBUG=True` short-circuits hashed-URL lookup entirely.

A `.dockerignore` was added alongside, because `collectstatic` publishes whatever the build context leaves under `static/`. It keeps `.env`, `.venv/`, `.git/`, the real `seed_data/*.csv`, and a working spreadsheet at `static/xlsx/` out of the image — the last of which would otherwise have been served, unauthenticated, at `/static/xlsx/inventory-system-as-is.xlsx`.

### 2. `.gitignore` — drop the blanket `seed_data/` line — **done**
The blanket `seed_data/` line is removed and the four `seed_data/*.example.csv` files are committed. The `static/` → `staticfiles/` correction described in Context #2 landed here too, together with `static/img/background.jpg` and an ignore for `static/xlsx/`.

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
- ~~`Dockerfile` — add `collectstatic` build step~~ — done
- ~~`config/settings.py` — move the static backend from the removed `STATICFILES_STORAGE` into `STORAGES`~~ — done
- ~~`.gitignore` — remove blanket `seed_data/` line; ignore `staticfiles/` not `static/`~~ — done
- ~~`.dockerignore` — new~~ — done
- `docker-compose.prod.yml` — new
- `.env.production.example` — new — must set `STATICFILES_BACKEND`? **No**: the `Dockerfile` `ENV` already covers it. Leave it out so there is one place to change it.
- `scripts/backup_db.sh` — new
- `DEPLOYMENT.md` — new

## Verification

Steps 1-3 cover the work already done and have been run:

1. ✅ `docker build -t materialinventory-test .` completes; the build log shows `200 static files copied to '/app/staticfiles', 574 post-processed` — the post-processing count is what proves manifest storage was actually active, rather than the build quietly succeeding with plain storage.
2. ✅ `git status` — the four `seed_data/*.example.csv` files are tracked, and so is `static/img/background.jpg`.
3. ✅ `python manage.py test` — 171 tests, `OK`.
4. ✅ End-to-end static check against the built image, the thing the original 404 report was about. Run it with `DEBUG=False` on the compose network and confirm:
   - `GET /static/img/logo.png` → **200** (was 404)
   - `GET /static/img/background.jpg` → **200**
   - `GET /login/` → **200**, with `img/logo.<hash>.png` and `img/background.<hash>.jpg` in the HTML — hashed names confirm the manifest is being read at runtime, not just written at build time
   - `GET /static/xlsx/inventory-system-as-is.xlsx` → **404**, and `/app/.env` absent from the image

Still outstanding, and only checkable on the server:

5. Real deployment check (runbook step 9) — loading the app from another device on the LAN.
6. Reboot check (runbook step 11) — confirms the DHCP reservation and `restart: unless-stopped` actually hold up, not just look right on paper.
