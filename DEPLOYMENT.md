# Deployment runbook — LAN-only, company server

Ordered steps to run **on the server**. Everything here is plain HTTP on the
depot LAN; the reasoning behind each choice is in
[DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md).

Steps 1–14 are the first install and run once. After that you only need
[Deploying an update](#deploying-an-update) and
[Routine maintenance](#routine-maintenance).

**What this deployment deliberately is not:** no domain, no HTTPS, no email, no
password-reset flow, no login rate limiting. Those are the things that would
have to be added before the app is reachable from the open internet — see
[Remote access](#remote-access-off-lan) for the supported way to reach it from
outside the depot.

---

## 1. Check the server

```bash
sudo ss -tlnp | grep LISTEN
```

Another web app and another Postgres already run on this machine. Confirm the
port you intend to use for `APP_PORT` (suggested: `8080`) is not in that list.

```bash
docker --version && docker compose version
```

Install Docker first if either is missing. Then confirm the daemon starts on
boot — this is what lets the containers come back by themselves after a reboot:

```bash
systemctl is-enabled docker
```

If that does not print `enabled`:

```bash
sudo systemctl enable docker
```

## 2. Pin the server's LAN IP

Ask whoever runs the office network for a **DHCP reservation** (or a static IP)
for this server's MAC address.

Do this before step 11. The IP is the address everyone bookmarks, and on plain
DHCP it can change on a lease renewal or a reboot — silently breaking every
saved URL with no error message anywhere.

## 3. Get the code

```bash
sudo mkdir -p /srv && cd /srv
git clone <repo-url> MaterialInventory
cd MaterialInventory
```

Every later command assumes you are in `/srv/MaterialInventory`.

## 4. Configure

```bash
cp .env.production.example .env.production
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

Edit `.env.production` and fill in:

| Key | Value |
|---|---|
| `SECRET_KEY` | the string just generated |
| `ALLOWED_HOSTS` | the reserved LAN IP, keeping `127.0.0.1,localhost` |
| `APP_BIND_IP` | the same reserved LAN IP |
| `APP_PORT` | `8080`, or whatever step 1 showed to be free |
| `DB_PASSWORD` | a strong password — **no `$` in it**, Compose interpolates this file |

Then set up the alias every following step uses:

```bash
alias dcp='docker compose --env-file .env.production -f docker-compose.prod.yml'
```

**Do not drop `--env-file`.** `env_file:` inside the compose file supplies the
container's environment; it does not feed the `${...}` interpolation that sets
the published port and the database credentials. Without the flag the database
comes up refusing to start (the `:?` guards) or on the wrong port.

Add the alias to `~/.bashrc` so it survives your next login.

## 5. First start

```bash
dcp up -d --build
dcp ps
```

Both services should be `Up`, `db` as `healthy`. The build runs `collectstatic`
inside the image, so there is nothing to collect by hand.

## 6. Verify the database collation — before any data exists

Postgres fixes its sort order when the cluster is created and **it cannot be
changed afterwards without a dump and restore**. This is the one step that is
cheap now and expensive in a month.

```bash
dcp exec db psql -U "$(grep '^DB_USER=' .env.production | cut -d= -f2-)" \
  -d "$(grep '^DB_NAME=' .env.production | cut -d= -f2-)" \
  -Atc "SELECT datlocprovider, daticulocale FROM pg_database WHERE datname = current_database();"
```

Expected output:

```
i|cs-CZ
```

`i` is the ICU provider. If you get `c` and an empty locale, the cluster came up
in byte order: Czech names sort wrong (`Štěrk` lands after `Zemina`), which is
visible in every material dropdown and on the Materiál page. Both catalogs order
by `name`, so this is not cosmetic.

Fixing it means throwing the empty cluster away and letting it initialise again:

```bash
dcp down -v          # DESTROYS the database volume — only safe before step 8
dcp up -d
```

Re-run the check. `down -v` after real data exists is data loss; from that point
the fix is dump → recreate → restore instead.

## 7. Create the schema

```bash
dcp exec web python manage.py migrate
```

## 8. Load the catalog and the staff accounts

The example files are committed and are **not equivalent**:

- `seed_data/materials.example.csv` and `machines.example.csv` already carry the
  real catalog — SKUs, machines, and the Kč rates. Copy them and spot-check the
  rates.
- `seed_data/users.example.csv` is placeholder rows (`worker.one`,
  `manager.one`, `admin.one`). Rewrite it with actual staff before using it.

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
cp seed_data/machines.example.csv seed_data/machines.csv
cp seed_data/users.example.csv seed_data/users.csv
# edit all three, then:
dcp exec web python manage.py seed_data
```

Read the output. `Materials: N created` is success. A line saying
`seed_data/materials.csv not found, skipping.` means the read-only `./seed_data`
mount is missing from the compose file — the command exits 0 either way, so it
will not fail on its own.

Every new user gets a random temporary password printed once. Capture them and
hand them out securely; they are not recoverable afterwards, and there is no
password-reset flow in this app (see step 9).

## 9. Create the admin accounts

```bash
dcp exec web python manage.py createsuperuser
```

Then open `http://<server-ip>:<APP_PORT>/admin/` and set that account's **role
to `ADMIN`**. `createsuperuser` leaves `role` at the `WORKER` default; superusers
bypass the role checks so the app still works, but an account whose displayed
role contradicts its access is a trap for whoever looks next.

**Create a second admin account.** There is no password-reset flow and no email
backend: a forgotten password is reset by another admin, or from the server with

```bash
dcp exec web python manage.py changepassword <username>
```

With one admin account and a forgotten password, that SSH command is the only
way back in.

## 10. Check the configuration

```bash
dcp exec web python manage.py check --deploy
```

Expect **exactly four** warnings — `SECURE_HSTS_SECONDS`, `SECURE_SSL_REDIRECT`,
`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`. All four are about HTTPS, which
this deployment deliberately does not use; setting either cookie flag would stop
the cookies being sent at all and nobody could log in.

Anything mentioning `SECRET_KEY` or `DEBUG` means `.env.production` did not take
— check that you passed `--env-file`.

## 11. Verify from the LAN

From a phone or laptop on the depot network, open:

```
http://<server-ip>:<APP_PORT>
```

Check all four:

1. The login page loads, with the logo and the background image.
2. You can log in.
3. Submitting one throwaway Zpracování job works.
4. The material dropdown lists `Štěrk` between `Struska` and `Zemina`, not after
   `Zemina` — the visible half of step 6.

A missing static manifest shows up here as a 500, not as a missing image.

## 12. Back up

```bash
./scripts/backup_db.sh
ls -lh backups/
```

Then add the daily cron entry:

```bash
crontab -e
```

```
15 2 * * * /srv/MaterialInventory/scripts/backup_db.sh >> /srv/MaterialInventory/backups/backup.log 2>&1
```

Use the crontab of a user that can talk to Docker — the one you have been
running `dcp` as. If `docker ps` needs `sudo` for that user, the cron job will
fail every night with a permission error in the log.

**The backups are on the same disk as the database.** That covers "someone
deleted a job" and not a dead disk. Arrange a copy onto another machine —
ideally pulled from that machine, so a problem here cannot delete both.

A cron job that stops working is silent. Check `backups/backup.log` and the
`ls -lh` timestamp occasionally; the script fails loudly on a short dump rather
than writing a truncated archive.

## 13. Rehearse a restore

Do this now, not on the day you need it.

```bash
DB_USER=$(grep '^DB_USER=' .env.production | cut -d= -f2-)
dcp exec db createdb -U "$DB_USER" restore_test
gunzip -c backups/<newest-file>.sql.gz | dcp exec -T db psql -U "$DB_USER" -d restore_test
dcp exec db psql -U "$DB_USER" -d restore_test -Atc "SELECT count(*) FROM workorders_workorder;"
dcp exec db dropdb -U "$DB_USER" restore_test
```

The count should match production. Write down whatever actually worked — that
note is the useful artifact at 8am on a bad day.

## 14. Reboot test

```bash
sudo reboot
```

When it comes back:

```bash
cd /srv/MaterialInventory && dcp ps
```

Both containers `Up` without anyone touching them, the app reachable at the same
LAN IP, and the throwaway job from step 11 still there.

---

## Deploying an update

```bash
cd /srv/MaterialInventory
./scripts/backup_db.sh          # always, and non-negotiable if the pull has a migration
git pull
dcp up -d --build               # rebuild: collectstatic runs in the image, not at boot
dcp exec web python manage.py migrate
```

Run `migrate` even when the change looks harmless. CI guarantees a migration
file exists for every model change; nothing guarantees it has been applied to
*this* database.

## Routine maintenance

| How often | Command | Why |
|---|---|---|
| Monthly | `dcp exec web python manage.py clearsessions` | Django's DB session table is never pruned automatically. |
| Monthly | `docker image prune -f` | Every `up --build` leaves a dangling image on a shared disk. |
| Monthly | `ls -lh backups/ && tail backups/backup.log` | Confirms the cron backup is still running. |
| As they arrive | Dependabot PRs | Security updates for Django, the base image and the actions. See the Postgres caveat below. |

## Upgrading Postgres

`docker-compose.prod.yml` pins `postgres:16-alpine`, and Dependabot will
eventually open a PR bumping it. **That PR is not a merge, it is a planned
maintenance window.** Postgres refuses to start on a data directory written by a
different major version, so deploying it as an ordinary update takes the app
down with a `db` container in a restart loop.

The upgrade is: back up → `dcp down -v` → bump the image → `dcp up -d` →
`migrate` → restore the dump. Note that a fresh cluster re-reads
`POSTGRES_INITDB_ARGS`, so step 6's check applies again.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `400 Bad Request` / `DisallowedHost` | The host in the URL is not in `ALLOWED_HOSTS`. Restart with `dcp up -d` after editing — `restart` keeps the old environment. |
| `db` exits with "set DB_NAME in .env.production" | The `--env-file` flag was left off. |
| `port is already allocated` | Something else took `APP_PORT`. Check with `ss -tlnp`, then change it in `.env.production`. |
| `500` on every page, `Missing staticfiles manifest entry` | The image was built without the `collectstatic` step. Rebuild with `dcp up -d --build`. |
| `seed_data` reports "not found, skipping." | The `./seed_data:/app/seed_data:ro` mount is missing, or the real `.csv` files were never created on the server. |
| Czech names sort after Z | The cluster was initialised without the ICU locale. See step 6 — before there is data, `dcp down -v` and start again. |
| `db` in a restart loop after an image update | Postgres major version change. See [Upgrading Postgres](#upgrading-postgres). |
| Containers gone after a reboot | The Docker daemon is not enabled at boot. See step 1. |
| A 500 with no traceback anywhere | Should not happen — `config/settings.py` logs `django.request` at ERROR to stdout. Read it with `dcp logs web`. |

## Remote access (off-LAN)

Do not port-forward this to the internet. The supported answer for a manager
working from home is a VPN (Tailscale), which puts their device on the same
private network without exposing the app, needing a domain, or opening a port.
The steps are in [DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md#remote-access-off-lan).
