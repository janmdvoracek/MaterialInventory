# Deployment runbook — public VPS, company domain

Ordered steps to run **on the VPS**. The app is served over HTTPS at the
company's domain, with Caddy terminating TLS in front of gunicorn; the reasoning
behind each choice is in [DEPLOYMENT_PLAN_PUBLIC.md](DEPLOYMENT_PLAN_PUBLIC.md).

Steps 1–16 are the first install and run once. After that you only need
[Deploying an update](#deploying-an-update) and
[Routine maintenance](#routine-maintenance).

**What this deployment still does not have:** no email backend and no
password-reset flow. Login rate limiting *is* in place — `django-axes`, see
[Lockouts](#lockouts) — and so is the `/admin/` gate, which is why only one of
the gaps the LAN perimeter used to cover is still open. Read
[Before you point DNS at it](#before-you-point-dns-at-it) **before** step 2, not
after step 16.

---

## Before you point DNS at it

One gap that the old LAN deployment closed with the network rather than with
code. It does not block the steps below, and it is cheaper to decide now than
after the URL has been handed out.

| Gap | Consequence once public | Options |
|---|---|---|
| **Seeded temporary passwords** | `seed_data` prints one random password per user; if they were handed out and never changed, they are now internet-facing credentials. | Reset every account before go-live, and require a change at first login. |

It is a judgement call rather than a blocker.

**The second gap, `/admin/` on the open internet, is closed in the app.**
`accounts/middleware.py::AdminSessionRequiredMiddleware` answers **404** for
every URL under `/admin/` — `/admin/login/` included — unless the request
already carries an admin's session, so the superuser login form is not served
on the internet at all and the app's own rate-limited `/login/` is the only
login form there is. Two consequences for the steps below:

- **You reach the admin by logging in to the app first**, at
  `https://<domain>/login/`, and then opening `https://<domain>/admin/` (or
  following the *Administrace* link in the header). Opening `/admin/` while
  logged out gives a 404, not a login page — that is the gate working, not a
  broken deployment.
- **The `Caddyfile`'s commented source-IP `route` block is now optional.** It
  restricts `/admin/` to known addresses on top of the gate; a VPN remains the
  thorough version. Neither is needed to keep the admin off the open web any
  more.

## 1. Provision and lock down the VPS

A small instance is plenty — this app is a handful of forms and four reports.
2 vCPU / 2 GB RAM comfortably runs `WEB_CONCURRENCY=3` alongside Postgres.

Before anything else, on a fresh box:

```bash
sudo apt update && sudo apt upgrade -y
```

Confirm SSH is key-only, then open just what is needed:

```bash
sudo ufw default deny incoming && sudo ufw allow OpenSSH && sudo ufw enable
```

**Do not add ufw rules for 80/443.** Docker publishes ports by writing DNAT
rules that sit *ahead* of ufw, so the proxy's ports are reachable whether ufw
lists them or not. The rule set above protects the *host's own* services — SSH
above all — and that is the job it can actually do here. The corollary matters
more: any port a compose file publishes is on the internet regardless of the
firewall, which is why neither `db` nor `web` publishes one, and why the
development `docker-compose.yml` — which publishes Postgres — must never be
brought up on this machine.

Then install Docker and confirm the daemon starts on boot; that is what brings
the containers back after a reboot:

```bash
docker --version && docker compose version && systemctl is-enabled docker
```

If the last one does not print `enabled`:

```bash
sudo systemctl enable docker
```

## 2. Point the domain at it

Ask whoever runs the company's DNS for an **A record** — and an AAAA record if
the VPS has IPv6 — pointing the chosen name, `inventar.firma.cz` say, at the
VPS's public IP.

Do this **before** step 6. Caddy proves control of the domain over port 80 to
obtain the certificate, so a name that does not yet resolve here means no
certificate and a proxy that will not serve.

Wait for it to propagate, then check from the VPS itself:

```bash
dig +short inventar.firma.cz
```

It must print this server's public IP and nothing else. A CNAME through a CDN or
a proxying DNS provider changes how the certificate is obtained; sort that out
now rather than debugging it in step 7.

## 3. Get the code

```bash
sudo mkdir -p /srv && cd /srv
```

```bash
git clone <repo-url> MaterialInventory && cd MaterialInventory
```

Every later command assumes you are in `/srv/MaterialInventory`.

## 4. Configure

```bash
cp .env.production.example .env.production
```

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

Edit `.env.production` and fill in:

| Key | Value |
|---|---|
| `SECRET_KEY` | the string just generated |
| `ALLOWED_HOSTS` | the domain, keeping `127.0.0.1,localhost` |
| `CSRF_TRUSTED_ORIGINS` | `https://<domain>` — **with the scheme** |
| `APP_DOMAIN` | the same domain, bare; Caddy requests the certificate for it |
| `ACME_EMAIL` | a monitored mailbox, not a personal one |
| `DB_PASSWORD` | a strong password — **no `$` in it**, Compose interpolates this file |

Leave `SECURE_SSL_REDIRECT`, `SECURE_COOKIES`, `BEHIND_PROXY` and the two HSTS
lines as the template has them. The comments in the file explain each; the one
to read twice is `SECURE_HSTS_SECONDS`, which starts at an hour on purpose.

Then set up the alias every following step uses:

```bash
alias dcp='docker compose --env-file .env.production -f docker-compose.prod.yml'
```

**Do not drop `--env-file`.** `env_file:` inside the compose file supplies the
container's environment; it does not feed the `${...}` interpolation that sets
the domain and the database credentials. Without the flag the `:?` guards stop
the stack with a named error.

Add the alias to `~/.bashrc` so it survives your next login.

## 5. Check the domain one more time

The certificate request in the next step is rate-limited by Let's Encrypt to
**5 per exact hostname per week**. A typo in `APP_DOMAIN` burns one of those.

```bash
grep -E '^(APP_DOMAIN|ALLOWED_HOSTS|CSRF_TRUSTED_ORIGINS)=' .env.production
```

All three must name the same host — `CSRF_TRUSTED_ORIGINS` with `https://`, the
other two without.

## 6. First start

```bash
dcp up -d --build && dcp ps
```

All three services should be `Up`, `db` as `healthy`. The build runs
`collectstatic` inside the image, so there is nothing to collect by hand.

## 7. Verify the certificate

```bash
dcp logs proxy | tail -30
```

Look for `certificate obtained successfully`. Repeated ACME failures instead
mean one of: the A record does not resolve here yet (step 2), port 80 is blocked
upstream by the provider's own firewall or security group, or `APP_DOMAIN` is
misspelt.

```bash
curl -sI https://inventar.firma.cz/login/ | head -1
```

Expect `HTTP/2 200`. Then confirm the redirect:

```bash
curl -sI http://inventar.firma.cz/ | head -1
```

Expect a `308`.

## 8. Verify the database collation — before any data exists

Postgres fixes its sort order when the cluster is created and **it cannot be
changed afterwards without a dump and restore**. This is the one step that is
cheap now and expensive in a month.

```bash
dcp exec db psql -U "$(grep '^DB_USER=' .env.production | cut -d= -f2-)" -d "$(grep '^DB_NAME=' .env.production | cut -d= -f2-)" -Atc "SELECT datlocprovider, daticulocale FROM pg_database WHERE datname = current_database();"
```

Expected output:

```
i|cs-CZ
```

`i` is the ICU provider. If you get `c` and an empty locale, the cluster came up
in byte order: Czech names sort wrong — `Štěrk` lands after `Zemina` — which is
visible in every material dropdown and on the Materiál page. Both catalogs order
by `name`, so this is not cosmetic.

Fixing it means throwing the empty cluster away and letting it initialise again:

```bash
dcp down && docker volume rm materialinventory-prod_db_data && dcp up -d
```

Naming the volume rather than reaching for `down -v` is deliberate: `down -v`
would take `caddy_data` with it, discarding the certificate you just obtained
and spending another of the five weekly issues. Re-run the check afterwards.
Once real data exists, this whole procedure becomes dump → recreate → restore.

## 9. Create the schema

```bash
dcp exec web python manage.py migrate
```

## 10. Load the catalog and the staff accounts

The example files are committed and are **not equivalent**:

- `seed_data/materials.example.csv` and `machines.example.csv` already carry the
  real catalog — SKUs, machines, and the Kč rates. Copy them and spot-check the
  rates.
- `seed_data/users.example.csv` is placeholder rows (`worker.one`,
  `manager.one`, `admin.one`). Rewrite it with actual staff before using it.
- `seed_data/locations.example.csv` is placeholder rows too (`Lokace 1`,
  `Lokace 2`). Replace them with the real sites. **Load at least one location**
  — here or in the admin — before anyone records a job: the Zpracování form
  requires one on every job that is not purely refuelling.

```bash
cp seed_data/materials.example.csv seed_data/materials.csv && cp seed_data/machines.example.csv seed_data/machines.csv && cp seed_data/locations.example.csv seed_data/locations.csv && cp seed_data/users.example.csv seed_data/users.csv
```

Edit all four, then:

```bash
dcp exec web python manage.py seed_data
```

Read the output. `Materials: N created` is success. A line saying
`seed_data/materials.csv not found, skipping.` means the read-only `./seed_data`
mount is missing from the compose file — the command exits 0 either way, so it
will not fail on its own.

Every new user gets a random temporary password printed once. **These are now
internet-facing credentials.** Hand them out securely, one per person, and treat
any password that has been read aloud or sent over chat as already compromised.

## 11. Create the admin accounts

```bash
dcp exec web python manage.py createsuperuser
```

Then log in at `https://<domain>/login/` with that account and open
`https://<domain>/admin/` — in that order, because the admin is a 404 to anyone
not already logged in as an admin (see
[Before you point DNS at it](#before-you-point-dns-at-it)). Set the account's
**role to `ADMIN`**. `createsuperuser` leaves `role` at the `WORKER` default;
superusers bypass the role checks so both the app and the admin still work, but
an account whose displayed role contradicts its access is a trap for whoever
looks next.

**Create a second admin account.** There is no password-reset flow and no email
backend: a forgotten password is reset by another admin, or from the server with

```bash
dcp exec web python manage.py changepassword <username>
```

With one admin account and a forgotten password, that SSH command is the only
way back in.

## 12. Check the configuration

```bash
dcp exec web python manage.py check --deploy
```

Expect **exactly one** warning: `security.W021` (`SECURE_HSTS_PRELOAD`). It is
left off deliberately — preloading submits the domain to a list compiled into
browsers themselves, and removal takes months and reaches users only as they
update. That is a decision about the company's whole domain, not about this app.

Anything else means something did not take:

- `W008` / `W012` / `W016` — `SECURE_SSL_REDIRECT` or `SECURE_COOKIES` is not
  `True` in `.env.production`.
- `W004` — `SECURE_HSTS_SECONDS` is still `0`.
- `SECRET_KEY` or `DEBUG` — `.env.production` did not load at all. Check the
  `--env-file` flag, and remember that `docker compose restart` keeps the old
  environment while `up -d` recreates the container.

## 13. Verify from the internet

From a device that is **not** on the office network — a phone on cellular data
is ideal — open `https://<domain>` and check all seven:

1. The browser shows a valid certificate with no warning.
2. The login page loads, with the logo and the background image.
3. You can log in. **This is the CSRF check**: a login that returns 403 "Origin
   checking failed" means `CSRF_TRUSTED_ORIGINS` is wrong or missing.
4. Submitting one throwaway Zpracování job works.
5. The material dropdown lists `Štěrk` between `Struska` and `Zemina`, not after
   `Zemina` — the visible half of step 8.
6. `http://<domain>` redirects to HTTPS rather than serving anything.
7. **In a private window, with nobody logged in**, `https://<domain>/admin/`
   answers **404** and shows no login form — and so does
   `https://<domain>/admin` without the slash, which must not redirect. A login
   form here means `AdminSessionRequiredMiddleware` is not in `MIDDLEWARE`; a
   301 on the slashless one means it was moved below `CommonMiddleware`.

A missing static manifest shows up here as a 500, not as a missing image.

## 14. Back up

```bash
./scripts/backup_db.sh && ls -lh backups/
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

**The backups are on the same disk as the database, and that disk is now
somebody else's.** A VPS can be lost whole: a billing lapse, a provider
incident, a mistaken rebuild. Pull `backups/` to a machine you control — a pull
from that machine is safer than a push from this one, because a compromise here
then cannot reach into the copy. The provider's own snapshots are a useful
second layer, not a substitute: they restore a disk, not a consistent dump.

A cron job that stops working is silent. Check `backups/backup.log` and the
`ls -lh` timestamp occasionally; the script fails loudly on a short dump rather
than writing a truncated archive.

## 15. Rehearse a restore

Do this now, not on the day you need it.

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

The count should match production. Write down whatever actually worked — that
note is the useful artifact at 8am on a bad day.

## 16. Reboot test

```bash
sudo reboot
```

When it comes back:

```bash
cd /srv/MaterialInventory && dcp ps
```

All three containers `Up` without anyone touching them, `https://<domain>`
serving with a valid certificate, and the throwaway job from step 13 still
there.

---

## Deploying an update

Two ways, and both run the same script. **From GitHub:** Actions → *Deploy* →
*Run workflow* on `main`, once the one-time setup in
[Automated deploys](#automated-deploys) is done. **By hand, on the server:**

```bash
cd /srv/MaterialInventory && ./scripts/deploy.sh
```

That deploys whatever `origin/main` points at; pass a full commit hash to deploy
a specific commit of `main` instead. In order, it:

1. fetches, and refuses a commit that is not on `origin/main`, one *older* than
   what is checked out, or a checkout carrying commits of its own;
2. runs `./scripts/backup_db.sh` — always, before anything changes;
3. fast-forwards the checkout;
4. builds the `web` image, which is where `collectstatic` runs — it does not run
   at boot;
5. runs `migrate` from a one-off container of the **new** image;
6. `up -d`, swapping the new container in.

Migrating before the swap means a broken build or a failed migration leaves the
old container serving; Postgres rolls a failed migration back. `migrate` runs
even when the change looks harmless: CI guarantees a migration file exists for
every model change, but nothing guarantees it has been applied to *this*
database.

**Rollbacks are refused on purpose.** Moving the code back does not move the
schema back, and an older commit can meet tables it does not understand. Roll
back by hand, deciding about the migrations first.

A deploy that failed halfway is finished by running it again: at the commit
already checked out, the script still rebuilds, migrates and restarts.

The proxy is left alone unless its compose definition changed, so an ordinary
update does not touch the certificate. An edit to `Caddyfile` alone still needs
`dcp up -d --force-recreate proxy`, because a changed bind-mounted file does not
count as a changed service.

### Automated deploys

`.github/workflows/deploy.yml` checks that CI passed for the code being deployed,
then connects over SSH and runs `scripts/deploy.sh`. The key it uses can run
that script and nothing else. The following is done once.

**1. A key for GitHub.** On your own machine, not the server:

```bash
ssh-keygen -t ed25519 -N '' -C github-deploy -f deploy_key
```

**2. Restrict it on the server.** Append `deploy_key.pub` to
`~/.ssh/authorized_keys` of the account that owns `/srv/MaterialInventory` and
can talk to Docker — the same one whose crontab runs the backup — with this
prefix on the same line:

```text
command="/srv/MaterialInventory/scripts/deploy.sh",restrict ssh-ed25519 AAAA… github-deploy
```

`command=` makes every connection with this key run the deploy script no matter
what the client asks for, and `restrict` removes port forwarding, agent
forwarding and a terminal. **That line is the whole security boundary**: an
account that can use Docker is root-equivalent, so the key must never go on the
server without it.

The server's own `git fetch` has to work without a prompt. It already does if
`git pull` has been working; the script sets `GIT_TERMINAL_PROMPT=0`, so a
missing credential fails the deploy instead of hanging it.

**3. Pin the host key.** Record the server's host key from a machine that has
already connected and checked it:

```bash
ssh-keygen -F <server> -f ~/.ssh/known_hosts
```

Take the matching lines without the `# Host … found` comment. If the entries are
hashed, `ssh-keyscan -t ed25519 <server>` gives an unhashed line — compare its
fingerprint (`ssh-keygen -lf -` reading that line) with
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` run on the server before
trusting it.

**4. A `production` environment on GitHub.** *Settings → Environments → New
environment*, named `production`:

- **Deployment branches and tags → Selected branches → `main`.** Not optional.
  Secrets on the repository would be readable by a workflow pushed on any
  branch; secrets on an environment limited to `main` are not.
- Secrets: `DEPLOY_SSH_KEY` (the whole private key file), `DEPLOY_KNOWN_HOSTS`
  (the lines from step 3), `DEPLOY_HOST`, `DEPLOY_USER`.
- Variables: `APP_DOMAIN` (the bare domain, as in `.env.production`), and
  `DEPLOY_PORT` only if SSH is not on 22.
- *Required reviewers* is optional. The run is already started by hand, but
  adding yourself makes a second click the confirmation.

Then delete `deploy_key` from your machine; GitHub holds the only copy it needs.

**Which commit it deploys.** The head of `main` when the run starts. CI skips
Markdown-only commits, so that head often has no CI run of its own. The workflow
instead finds the newest commit on `main` whose CI passed and requires that
everything after it is Markdown. A deploy started while CI for the latest push
is still running, or after it failed, stops with an error that names the
untested files. Wait for CI and run it again.

After the SSH step it fetches `https://<domain>/login/` from GitHub's side —
through DNS, the certificate and the proxy, the way a user arrives.

To take the key out of use, delete its line from `authorized_keys`. That ends
it immediately, whatever GitHub still holds.

## Lockouts

Login rate limiting is `django-axes`. **Five failed logins lock the account for
30 minutes**, and one successful login clears the counter.

It locks the *username*, not the address — deliberately. The depot reaches the
app through one office NAT address, and behind Caddy every request looks like it
came from the proxy container, so locking by IP would let one worker mistyping
their password take the whole depot offline. The trade-off is that an attacker
gets five guesses per account per cool-off from as many addresses as they like.

To unlock someone who is locked out and cannot wait:

```bash
dcp exec web python manage.py axes_reset_username <username>
```

To see what has been tried:

```bash
dcp exec web python manage.py axes_list_attempts
```

That command is how you read the log, because the axes admin section is turned
off: it ships no Czech translation and would be the only English in the admin.

Two things worth knowing before you change any of this. `manage.py check` prints
`axes.W006` complaining that the lockout is not by IP — that is answered on
purpose in `SILENCED_SYSTEM_CHECKS`, so it is silenced rather than ignored, and
`check --deploy` still returns its single expected `W021`. And the lockout page
(`templates/registration/lockout.html`) tells the worker "přibližně za 30 minut"
in prose, so changing `AXES_COOLOFF_TIME` means changing the template too.

## Routine maintenance

| How often | Command | Why |
|---|---|---|
| Weekly | `sudo apt update && sudo apt upgrade` | The host is on the internet now. Security updates are not optional. |
| Monthly | `dcp exec web python manage.py clearsessions` | Django's DB session table is never pruned automatically. |
| Monthly | `docker image prune -f` | Every `up --build` leaves a dangling image. |
| Monthly | `ls -lh backups/ && tail backups/backup.log` | Confirms the cron backup is still running. |
| Monthly | `curl -sI https://<domain>/login/` | Confirms the certificate renewed. Caddy does it at 60 days unattended; this is how you find out it stopped. |
| As they arrive | Dependabot PRs | Security updates for Django, the base images and the actions. See the Postgres caveat below. |

## Upgrading Postgres

`docker-compose.prod.yml` pins `postgres:16-alpine`, and Dependabot will
eventually open a PR bumping it. **That PR is not a merge, it is a planned
maintenance window.** Postgres refuses to start on a data directory written by a
different major version, so deploying it as an ordinary update takes the app
down with a `db` container in a restart loop.

The upgrade is: back up → `dcp down` → `docker volume rm
materialinventory-prod_db_data` → bump the image → `dcp up -d` → `migrate` →
restore the dump. Remove the *named* volume rather than using `down -v`, which
would also destroy `caddy_data` and force a fresh certificate issue. A fresh
cluster re-reads `POSTGRES_INITDB_ARGS`, so step 8's check applies again.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `500` on login or logout, `relation "axes_accessattempt" does not exist` | `migrate` was not run after a pull that added `django-axes`. It brings two models of its own. `dcp exec web python manage.py migrate`. |
| A worker says they cannot log in and the password is right | They are locked out; five failures locks an account for 30 minutes. See [Lockouts](#lockouts) to clear it. |
| Every login attempt in `axes_list_attempts` shows the same IP | `BEHIND_PROXY=True` is missing from `.env.production`, so axes is recording the proxy container instead of the client. Costs the audit trail only — lockouts are by username and still correct. |
| Login, or any form, returns `403` "Origin checking failed" | `CSRF_TRUSTED_ORIGINS` is missing or lacks the `https://` scheme. Pages load fine, which is what makes this look like a working deployment. |
| Every request is an infinite redirect loop | `X-Forwarded-Proto` is not reaching Django, so `SECURE_SSL_REDIRECT` keeps redirecting an already-HTTPS request. Check `FORWARDED_ALLOW_IPS` is still `*` on the `web` service and that nothing re-published its port. |
| `400 Bad Request` / `DisallowedHost` | The host in the URL is not in `ALLOWED_HOSTS`. Restart with `dcp up -d` after editing — `restart` keeps the old environment. |
| Certificate warning in the browser; `dcp logs proxy` shows ACME failures | DNS does not resolve to this server, or port 80 is blocked by the provider's firewall. Both are outside the compose file. |
| Site was fine, now the certificate has expired | Port 80 was closed at some point after the first issue. Renewal needs it just as much as issue does. |
| `db` exits with "set DB_NAME in .env.production" | The `--env-file` flag was left off. |
| `port is already allocated` on 80 or 443 | Something else on the VPS is already serving. Nothing else should be. |
| `500` on every page, `Missing staticfiles manifest entry` | The image was built without the `collectstatic` step. Rebuild with `dcp up -d --build`. |
| `seed_data` reports "not found, skipping." | The `./seed_data:/app/seed_data:ro` mount is missing, or the real `.csv` files were never created on the server. |
| Czech names sort after Z | The cluster was initialised without the ICU locale. See step 8. |
| `db` in a restart loop after an image update | Postgres major version change. See [Upgrading Postgres](#upgrading-postgres). |
| Containers gone after a reboot | The Docker daemon is not enabled at boot. See step 1. |
| A 500 with no traceback anywhere | Should not happen — `config/settings.py` logs `django.request` at ERROR to stdout. Read it with `dcp logs web`. |
