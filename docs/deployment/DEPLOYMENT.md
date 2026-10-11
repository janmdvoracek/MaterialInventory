# Deployment runbook

Steps to run **on the VPS**. The app is served over HTTPS on the company domain, with Caddy in front of gunicorn. The reasoning behind the settings is in [configuration.md](../configuration.md).

Steps 1–16 are the first install. After that you only need [Deploying an update](#deploying-an-update) and [Routine maintenance](#routine-maintenance).

## Before you point DNS at it

**One open security gap: seeded temporary passwords.** `seed_data` prints a random password for each new user. Any that were handed out and never changed are now internet-facing credentials. Reset every account before go-live and have people change their password at first login.

What is already covered:

- **Login rate limiting.** `django-axes`; see [Lockouts](#lockouts).
- **The admin.** `/admin/` answers **404** to anyone not already logged in as an admin, `/admin/login/` included. To reach it, log in at `https://<domain>/login/` first, then open `/admin/` or follow the *Administrace* header link. A 404 while logged out is the gate working.

The `Caddyfile`'s commented source-IP block, or a VPN, is an optional extra layer for the admin. Neither is required.

The deployment has no email backend and no password-reset flow. Admins reset passwords for each other.

## 1. Provision and lock down the VPS

2 vCPU / 2 GB RAM is plenty.

```bash
sudo apt update && sudo apt upgrade -y
```

Confirm SSH accepts keys only, then:

```bash
sudo ufw default deny incoming && sudo ufw allow OpenSSH && sudo ufw enable
```

**Don't add ufw rules for 80/443.** Docker's published ports bypass ufw, so the proxy is reachable regardless. The flip side is that **any port a compose file publishes is on the internet**. That is why neither `db` nor `web` publishes one, and why the dev `docker-compose.yml`, which publishes Postgres, must never run here.

Install Docker and make sure it starts on boot; that is what brings the containers back after a reboot:

```bash
docker --version && docker compose version && systemctl is-enabled docker
```

If the last command doesn't print `enabled`:

```bash
sudo systemctl enable docker
```

## 2. Point the domain at it

Get an **A record** for the chosen name (`inventar.firma.cz`, say) pointing at the VPS's public IP. Add an AAAA record too if the VPS has IPv6. Do this **before step 6**: Caddy proves control of the domain over port 80.

```bash
dig +short inventar.firma.cz
```

It must print this server's IP and nothing else. A CNAME through a CDN or a proxying DNS service changes how certificates are issued; sort that out now.

## 3. Get the code

```bash
sudo mkdir -p /srv && cd /srv
```

```bash
git clone <repo-url> MaterialInventory && cd MaterialInventory
```

Every later command runs in `/srv/MaterialInventory`.

## 4. Configure

```bash
cp .env.production.example .env.production
```

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

Fill in `.env.production`:

| Key | Value |
|---|---|
| `SECRET_KEY` | the string just generated |
| `ALLOWED_HOSTS` | the domain, keeping `127.0.0.1,localhost` |
| `CSRF_TRUSTED_ORIGINS` | `https://<domain>`, **with the scheme** |
| `APP_DOMAIN` | the bare domain |
| `ACME_EMAIL` | a monitored shared mailbox |
| `DB_PASSWORD` | a strong password **without `$`** (Compose interpolates the file) |

Leave the HTTPS lines as they are in the template. `SECURE_HSTS_SECONDS` starts at one hour on purpose.

Set up the alias every later step uses, and add it to `~/.bashrc`:

```bash
alias dcp='docker compose --env-file .env.production -f docker-compose.prod.yml'
```

**Never drop `--env-file`.** The compose file's `${...}` variables (domain, DB credentials) read only from it. Without it, the `:?` guards stop the stack with a named error.

## 5. Check the domain one more time

Let's Encrypt allows **5 certificates per hostname per week**, and a typo in `APP_DOMAIN` uses one up.

```bash
grep -E '^(APP_DOMAIN|ALLOWED_HOSTS|CSRF_TRUSTED_ORIGINS)=' .env.production
```

All three must name the same host: `CSRF_TRUSTED_ORIGINS` with `https://`, the other two without.

## 6. First start

```bash
dcp up -d --build && dcp ps
```

All three services should be `Up`, with `db` reported as `healthy`. The image build runs `collectstatic`.

## 7. Verify the certificate

```bash
dcp logs proxy | tail -30
```

Look for `certificate obtained successfully`. If ACME keeps failing, one of these is wrong:

- DNS doesn't point here yet (step 2);
- the provider blocks port 80;
- `APP_DOMAIN` is misspelt.

```bash
curl -sI https://inventar.firma.cz/login/ | head -1
```

Expect `HTTP/2 200`. Then:

```bash
curl -sI http://inventar.firma.cz/ | head -1
```

Expect `308`.

## 8. Verify the database collation, before any data exists

The sort order is fixed when the cluster is created, and changing it later means dump and restore.

```bash
dcp exec db psql -U "$(grep '^DB_USER=' .env.production | cut -d= -f2-)" -d "$(grep '^DB_NAME=' .env.production | cut -d= -f2-)" -Atc "SELECT datlocprovider, daticulocale FROM pg_database WHERE datname = current_database();"
```

Expect `i|cs-CZ`. If you get `c` and an empty locale, Czech names will sort wrong (`Štěrk` after `Zemina`). Recreate the still-empty cluster:

```bash
dcp down && docker volume rm materialinventory-prod_db_data && dcp up -d
```

**Name the volume. Don't use `down -v`**, which also deletes `caddy_data` (the certificate, costing another weekly issue) and `media_data` (every photo). Re-run the check afterwards.

## 9. Create the schema

```bash
dcp exec web python manage.py migrate
```

## 10. Load the catalog and staff accounts

- `materials.example.csv` and `machines.example.csv` already hold the real catalog and rates. Copy them and spot-check.
- `users.example.csv` and `locations.example.csv` are placeholders. Rewrite them with real staff and sites. **At least one location must exist before anyone records a job**, because every non-fuel job requires one.

```bash
cp seed_data/materials.example.csv seed_data/materials.csv && cp seed_data/machines.example.csv seed_data/machines.csv && cp seed_data/locations.example.csv seed_data/locations.csv && cp seed_data/users.example.csv seed_data/users.csv
```

Edit all four, then:

```bash
dcp exec web python manage.py seed_data
```

`Materials: N created` means success. `seed_data/materials.csv not found, skipping.` means the `./seed_data` mount is missing; the command exits 0 either way.

Each new user's temporary password is printed once. **These are internet-facing credentials.** Hand them out one per person, securely.

## 11. Create the admin accounts

```bash
dcp exec web python manage.py createsuperuser
```

Log in at `https://<domain>/login/`, then open `/admin/`, in that order. Set the account's **role to `ADMIN`**: `createsuperuser` leaves it at `WORKER`, which still works for a superuser but misleads whoever looks next.

**Create a second admin.** With no reset flow, a forgotten password otherwise means:

```bash
dcp exec web python manage.py changepassword <username>
```

## 12. Check the configuration

```bash
dcp exec web python manage.py check --deploy
```

Expect **exactly one** warning, `security.W021` (HSTS preload, deliberately off). Anything else:

- `W008` / `W012` / `W016`: `SECURE_SSL_REDIRECT` or `SECURE_COOKIES` isn't `True`.
- `W004`: `SECURE_HSTS_SECONDS` is `0`.
- `SECRET_KEY` or `DEBUG`: `.env.production` didn't load. Check `--env-file`. `restart` keeps the old environment; `up -d` recreates the container.

## 13. Verify from the internet

From a device **off** the office network (a phone on mobile data), check:

1. The certificate is valid.
2. The login page loads, with the logo and background.
3. You can log in. A 403 "Origin checking failed" means `CSRF_TRUSTED_ORIGINS` is wrong.
4. A throwaway Zpracování job saves.
5. The material dropdown lists `Štěrk` between `Struska` and `Zemina`.
6. `http://<domain>` redirects to HTTPS.
7. In a private window, `https://<domain>/admin/` and `/admin` (no slash) both answer **404** with no redirect. A login form means the middleware is missing; a 301 on the slashless URL means it moved below `CommonMiddleware`.

A missing static manifest shows up as a 500 on every page.

## 14. Back up

```bash
./scripts/backup_db.sh && ls -lh backups/
```

Add a daily cron entry, as a user that can run Docker without `sudo`:

```bash
crontab -e
```

```
15 2 * * * /srv/MaterialInventory/scripts/backup_db.sh >> /srv/MaterialInventory/backups/backup.log 2>&1
```

**The dump doesn't include photos.** They live in the `materialinventory-prod_media_data` volume. To archive them alongside:

```bash
docker run --rm -v materialinventory-prod_media_data:/media -v "$PWD/backups:/out" alpine tar czf /out/media-$(date +%F).tar.gz -C /media .
```

**Copy the backups off the server.** A VPS can be lost whole. Pull from a machine you control rather than pushing from here, so a compromised server can't reach the copies. Provider snapshots are a second layer, not a substitute.

A cron job that stops is silent, so check `backups/backup.log` now and then. The script fails loudly on a short dump.

## 15. Rehearse a restore

```bash
DB_USER=$(grep '^DB_USER=' .env.production | cut -d= -f2-)
```

```bash
dcp exec db createdb -U "$DB_USER" restore_test
```

```bash
gunzip -c backups/<newest-file>.sql.gz | dcp exec -T db psql -U "$DB_USER" -d restore_test
```

```bash
dcp exec db psql -U "$DB_USER" -d restore_test -Atc "SELECT count(*) FROM workorders_workorder;"
```

```bash
dcp exec db dropdb -U "$DB_USER" restore_test
```

The count should match production. Write down what worked.

## 16. Reboot test

```bash
sudo reboot
```

```bash
cd /srv/MaterialInventory && dcp ps
```

All three containers should be `Up` on their own, the site serving with a valid certificate, and step 13's test job still there.

---

## Deploying an update

Either from GitHub (Actions → *Deploy* → *Run workflow* on `main`, once [Automated deploys](#automated-deploys) is set up), or on the server:

```bash
cd /srv/MaterialInventory && ./scripts/deploy.sh
```

That deploys `origin/main`; pass a full commit hash to deploy a specific commit of `main`. The script:

1. fetches, and refuses a commit that is not on `origin/main`, is older than `HEAD`, or a checkout with local commits;
2. runs `backup_db.sh`;
3. fast-forwards;
4. builds the `web` image (`collectstatic` runs here);
5. runs `migrate` from the **new** image;
6. runs `up -d` to swap the container;
7. runs `docker image prune -f` (non-fatal).

Because migrations run before the swap, a failed build or migration leaves the old version serving.

- **Rollbacks are refused.** The schema doesn't roll back with the code, so a rollback is done by hand, deciding about migrations first.
- **A half-finished deploy is finished by re-running the script**; at the current commit it still rebuilds, migrates and restarts.
- **Caddy is untouched** unless its compose definition changed. An edit to the `Caddyfile` alone needs `dcp up -d --force-recreate proxy`.

### Automated deploys

`.github/workflows/deploy.yml` checks that CI passed, then SSHes in and runs `deploy.sh`. The key can run nothing else. One-time setup:

**1. Create a key**, on your own machine:

```bash
ssh-keygen -t ed25519 -N '' -C github-deploy -f deploy_key
```

**2. Restrict it on the server.** Append `deploy_key.pub` to `~/.ssh/authorized_keys` of the account that owns the checkout and runs Docker, with this prefix on the same line:

```text
command="/srv/MaterialInventory/scripts/deploy.sh",restrict ssh-ed25519 AAAA… github-deploy
```

**That line is the whole security boundary.** A Docker-capable account is root-equivalent, so never add the key without it. `deploy.sh` reads only a 40-character commit hash from `SSH_ORIGINAL_COMMAND`. The server's own `git fetch` must work without a prompt; the script sets `GIT_TERMINAL_PROMPT=0`.

**3. Pin the host key** from a machine that has already verified it:

```bash
ssh-keygen -F <server> -f ~/.ssh/known_hosts
```

If the entries are hashed, use `ssh-keyscan -t ed25519 <server>`. Before trusting it, compare its fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` run on the server.

**4. Create a GitHub environment** called `production` (Settings → Environments):

- **Deployment branches: `main` only.** This is not optional, since repository-level secrets would be readable from any branch.
- Secrets: `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`, `DEPLOY_HOST`, `DEPLOY_USER`.
- Variables: `APP_DOMAIN`, and `DEPLOY_PORT` if SSH isn't on 22.
- Required reviewers are optional.

Then delete `deploy_key` locally.

**Which commit gets deployed.** The head of `main`. CI skips Markdown-only commits, so the workflow finds the newest `main` commit whose CI passed and requires everything after it to be `*.md`. If CI is still running or failed, the deploy stops and names the untested files. Afterwards it fetches `https://<domain>/login/` as a user would.

To revoke the key, delete its line from `authorized_keys`.

## Lockouts

Five failed logins lock an **account** (not an IP address) for 30 minutes; a successful login clears the count. Locking by IP would let one worker's typo lock out the whole depot, which shares one address.

```bash
dcp exec web python manage.py axes_reset_username <username>   # unlock now
dcp exec web python manage.py axes_list_attempts               # read the log
```

The axes admin section is off (it has no Czech translation). `axes.W006` is silenced on purpose. `templates/registration/lockout.html` quotes "30 minutes", so change it together with `AXES_COOLOFF_TIME`.

## Routine maintenance

| When | Command | Why |
|---|---|---|
| Weekly | `sudo apt update && sudo apt upgrade` | security updates |
| Monthly | `dcp exec web python manage.py clearsessions` | Django never prunes sessions |
| Monthly | `df -h /` | the disk is 20 GB; photos, backups and Docker's build cache grow |
| Monthly | `ls -lh backups/ && tail backups/backup.log` | the backup cron is still running |
| Monthly | `curl -sI https://<domain>/login/` | the certificate renewed |
| After a manual build | `docker image prune -f` | `deploy.sh` prunes; manual builds don't |
| As they arrive | Dependabot PRs | but see Postgres below |

## Upgrading Postgres

`docker-compose.prod.yml` pins `postgres:16-alpine`. **A major-version bump is a maintenance window, not a merge.** Postgres won't start on a data directory from another major version.

The procedure:

1. Back up.
2. `dcp down`.
3. `docker volume rm materialinventory-prod_db_data` (never `down -v`).
4. Bump the image.
5. `dcp up -d`.
6. Run `migrate`.
7. Restore the dump.

The fresh cluster re-reads `POSTGRES_INITDB_ARGS`, so repeat step 8's check.

## Troubleshooting

| Symptom | Cause |
|---|---|
| 500 on login or logout, `relation "axes_accessattempt" does not exist` | `migrate` wasn't run |
| A worker can't log in with the right password | They are locked out; see [Lockouts](#lockouts) |
| Every attempt in `axes_list_attempts` shows the same IP | `BEHIND_PROXY=True` is missing (affects the audit trail only) |
| 403 "Origin checking failed" on login or any form | `CSRF_TRUSTED_ORIGINS` is missing or lacks `https://` |
| Infinite redirect loop | `X-Forwarded-Proto` isn't reaching Django. Check `FORWARDED_ALLOW_IPS: "*"` on `web`, and that `web` has no `ports:` |
| 400 `DisallowedHost` | The host isn't in `ALLOWED_HOSTS`. Run `dcp up -d` after editing |
| Certificate warning; ACME errors in `dcp logs proxy` | DNS doesn't point here, or port 80 is blocked |
| Certificate expired after working | Port 80 was closed later; renewal needs it too |
| `db` exits with "set DB_NAME in .env.production" | The `--env-file` flag is missing |
| `port is already allocated` on 80/443 | Something else on the VPS is serving |
| 500 everywhere, `Missing staticfiles manifest entry` | The image was built without `collectstatic`; run `dcp up -d --build` |
| Photo links answer 404 | The `media_data:/app/media` mount is missing; those files are gone |
| 413 when submitting photos | Over Caddy's 25 MB `request_body` cap. Smaller oversize batches get the app's Czech error instead |
| `seed_data`: "not found, skipping." | The `./seed_data` mount is missing, or the real CSVs don't exist |
| Czech names sort after Z | Collation; see step 8 |
| `db` in a restart loop after an update | Postgres major version change; see above |
| Containers gone after a reboot | Docker isn't enabled at boot; see step 1 |
| A 500 with no traceback | Read `dcp logs web`; `django.request` logs errors to stdout |
