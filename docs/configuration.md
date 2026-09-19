# Configuration

Settings live in `config/settings.py` and read from the environment via
[python-decouple](https://github.com/HBNetwork/python-decouple). Every setting
has a working default, so the app starts from a bare checkout with no `.env` —
which is what makes `collectstatic` safe to run at Docker build time.

**Precedence: real environment variables beat `.env`, which beats the default.**
That is why the `Dockerfile`'s `ENV` lines win over a mounted `.env`.

---

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | an insecure literal | **Must be set in production.** Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`. |
| `DEBUG` | `True` | Must be `False` in production. Also disables Django's static-file serving — see below. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated **host names, no scheme**. In production this is the company domain. |
| `CSRF_TRUSTED_ORIGINS` | empty | Comma-separated, **each entry with a scheme** (`https://inventar.firma.cz`); a leading wildcard is allowed (`https://*.loca.lt`). **Required** whenever anything terminates TLS in front of Django — see below. |
| `SECURE_SSL_REDIRECT` | `False` | Redirect plain HTTP to HTTPS. See [HTTPS](#https-and-running-behind-a-proxy). |
| `SECURE_COOKIES` | `False` | Sets `SESSION_COOKIE_SECURE` **and** `CSRF_COOKIE_SECURE` together. |
| `SECURE_HSTS_SECONDS` | `0` | HSTS max-age. Not reversible by editing this file — read the section below before raising it. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `False` | Safe on a subdomain deployment; a policy decision at the apex. |
| `SECURE_HSTS_PRELOAD` | `False` | Left off deliberately; `check --deploy` reports `W021` and that is expected. |
| `BEHIND_PROXY` | `False` | Makes django-axes read the client address from `X-Forwarded-For` instead of `REMOTE_ADDR`. See [Login rate limiting](#login-rate-limiting). |
| `DB_NAME` | `materialinventory` | |
| `DB_USER` | `materialinventory` | |
| `DB_PASSWORD` | `materialinventory` | Change in production. |
| `DB_HOST` | `localhost` | `.env.example` sets `db` for Compose. Use `localhost` when running Django on the host. |
| `DB_PORT` | `5432` | |
| `STATICFILES_BACKEND` | plain `StaticFilesStorage` | Set by the `Dockerfile`. See below. |

`.env` is gitignored; `.env.example` is the committed template.

### Deployment-only variables

These are read by `docker-compose.prod.yml` and by gunicorn — not by
`config/settings.py`, so they have no effect on `runserver`. The committed
template is `.env.production.example`; the filled-in `.env.production` is
gitignored and excluded from the image.

| Variable | Default | Notes |
|---|---|---|
| `APP_DOMAIN` | none — `:?` guard | The host Caddy requests a certificate for. Must already resolve to this server before the first `up`: Caddy validates over port 80. |
| `ACME_EMAIL` | none — `:?` guard | Let's Encrypt account address; where the warning goes if renewal ever stops working. |
| `WEB_CONCURRENCY` | `1` (gunicorn's own default) | Gunicorn worker count. One worker serializes every request, so production sets `3`. Read natively by gunicorn, which is why the `Dockerfile` `CMD` needs no override. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` (gunicorn's own default) | Set to `*` in `docker-compose.prod.yml`, not in `.env.production`. See [HTTPS](#https-and-running-behind-a-proxy) — the default silently breaks the whole deployment. |

`APP_BIND_IP` and `APP_PORT` are **gone**. They existed because the LAN
deployment published gunicorn on the host and had to pin it to the depot
interface. The public deployment publishes no port for `web` at all — Caddy owns
80/443 and reaches gunicorn over the Compose network — so there is no longer a
port to bind or to choose.

**`--env-file` is mandatory** for every command against
`docker-compose.prod.yml`. A service's `env_file:` key populates the
*container's* environment; it does not feed the `${...}` interpolation in the
compose file itself, which reads only the shell environment and the project's
default `.env`. Without the flag, `APP_DOMAIN` and the database credentials
expand empty — the `:?` guards on the `db` and `proxy` services turn that into a
loud error rather than a mysterious one.

## `CSRF_TRUSTED_ORIGINS` and TLS in front of Django

Empty is correct only when the setup is plain HTTP end to end — local
`runserver`, and the LAN deployment this project used to have. Django then
compares a request's `Origin` header against its own host, and the
`ALLOWED_HOSTS` entry is all the configuration CSRF needs.

**The production deployment is no longer one of those**, and this is the setting
most likely to be got wrong there. Caddy terminates TLS and speaks plain HTTP to
gunicorn, so `.env.production` must carry `CSRF_TRUSTED_ORIGINS=https://<domain>`
or every POST fails. The rest of this section is why.

It stops being correct as soon as **something terminates TLS in front of
Django** — a reverse proxy, or a tunnel used to reach a dev server from a phone
(localtunnel, Cloudflare Tunnel, ngrok). The browser talks HTTPS to the tunnel,
the tunnel talks plain HTTP to Django, so the browser sends
`Origin: https://<host>` while Django builds `http://<host>` as the origin it
expects. They differ by scheme, and **every POST fails with a 403 and "Origin
checking failed"** — login included. Pages still load, which is what makes this
look like a working setup at first.

Adding the host to `ALLOWED_HOSTS` does not fix it: that setting only decides
whether the request is answered at all (a miss is a `400 DisallowedHost`), and
plays no part in the CSRF origin check.

For localtunnel specifically:

```bash
ALLOWED_HOSTS=localhost,127.0.0.1,.loca.lt
CSRF_TRUSTED_ORIGINS=https://*.loca.lt
```

The wildcards matter because localtunnel hands out a **new random subdomain on
every run** unless it is started with `--subdomain <name>`; without them both
lines go stale each restart. `ALLOWED_HOSTS` spells its wildcard as a leading
dot, `CSRF_TRUSTED_ORIGINS` as a `*` after the scheme — the two settings do not
share a syntax.

Two things that are not configuration problems and cannot be fixed here:
`.loca.lt` shows every first-time visitor an interstitial demanding the tunnel
password (the tunnel host's public IP, from <https://loca.lt/mytunnelpassword>),
so each phone hits that before reaching Django; and under Compose `env_file` is
read when the container is **created**, so an edited `.env` needs
`docker compose up -d` to recreate the container — `docker compose restart`
keeps the old values.

Do **not** reach for `SECURE_PROXY_SSL_HEADER` *instead*. The two settings solve
different problems — one tells Django the request arrived over HTTPS, the other
tells it which origins to accept a POST from — and neither substitutes for the
other. `config/settings.py` does set `SECURE_PROXY_SSL_HEADER`; see the next
section for the conditions that make that safe, all of which a `runserver`
behind a dev tunnel fails.

## HTTPS and running behind a proxy

Production terminates TLS in Caddy and speaks plain HTTP to gunicorn on the
Compose network. Four pieces have to agree, and three of them fail in ways that
do not point at themselves.

### The settings default to off, on purpose

`SECURE_SSL_REDIRECT`, `SECURE_COOKIES` (both cookie flags) and the three HSTS
settings all read from the environment and default to off. This is the same trap
[the static-files default](#the-default-is-plain-storage-on-purpose) carries, for
the same reason: **Django's test runner forces `DEBUG=False`**, so a hardcoded
`SECURE_SSL_REDIRECT = True` turns every `self.client.get()` in the suite into a
301 and fails essentially every test in the project. Production opts in through
`.env.production`.

### `SECURE_PROXY_SSL_HEADER` is set unconditionally, and why that is safe

It makes Django trust `X-Forwarded-Proto`, which is a "pretend I am on HTTPS"
switch for anyone who can set the header. Two conditions in
`docker-compose.prod.yml` are what keep it honest, and they must stay true
together:

1. The `web` service publishes **no host port** (`expose:`, not `ports:`), so
   the proxy is the only thing that can connect to gunicorn.
2. Caddy **overwrites** `X-Forwarded-*` on every request rather than passing a
   client's through.

Re-publish that port and the setting becomes forgeable in the same edit. That is
also exactly why it must not be set on a `runserver` reached through localtunnel
or ngrok, where anyone can send the header straight to Django.

### `FORWARDED_ALLOW_IPS` — the failure that looks like something else

Gunicorn discards `X-Forwarded-*` from any peer not in this list, and its default
is `127.0.0.1` — which the proxy is *not*, being a separate container with its
own address on the Compose network.

Leave it at the default and: the header never reaches Django →
`SECURE_PROXY_SSL_HEADER` never fires → `request.is_secure()` stays `False` →
`SECURE_SSL_REDIRECT` redirects an already-HTTPS request to HTTPS, forever. The
symptom is an infinite redirect loop that looks like a broken reverse proxy, and
the fix is in neither the proxy config nor Django's settings.

`docker-compose.prod.yml` therefore sets `FORWARDED_ALLOW_IPS: "*"` on the `web`
service. `*` is acceptable only because of condition 1 above.

### HSTS is the one thing here you cannot take back

A browser that has seen the header refuses plain HTTP for that host until the
max-age expires, no matter what the server later sends. A year of that on a
domain whose certificate has broken is a year of an unreachable app. Start at
`3600`, raise to `31536000` once a renewal has actually happened.

`SECURE_HSTS_INCLUDE_SUBDOMAINS` is safe on a **subdomain** deployment — it
covers `inventar.firma.cz` and anything beneath it and does not propagate up to
`firma.cz`. At the apex it would commit every company subdomain to HTTPS.

`SECURE_HSTS_PRELOAD` is left off, so `manage.py check --deploy` reports exactly
one warning (`W021`) rather than none. Preloading submits the domain to a list
compiled into browsers themselves; removal takes months and reaches users only
as they update.

## Login rate limiting

`django-axes`, added when the app moved off the LAN. Django ships no
brute-force protection, and the perimeter used to stand in for it.

**Five failed logins lock the account for 30 minutes**, and a successful login
clears the counter (`AXES_RESET_ON_SUCCESS`). The failure limit is five rather
than the library's three because these are phone keyboards and gloved hands.

### It locks the username, not the address

`AXES_LOCKOUT_PARAMETERS = ['username']`. The library's default is
`['ip_address']`, and here that would have been actively harmful: depot staff
reach the app through a handful of shared egress addresses — the office NAT, a
mobile carrier — and behind the reverse proxy every request that axes has not
been told about looks like it came from the proxy container. Locking by IP would
therefore let one worker mistyping their password lock out the whole depot,
while barely inconveniencing an attacker with a list of addresses.

The trade-off is real: an attacker gets five guesses per account per cool-off
from as many addresses as they like, and someone who learns a username can lock
that person out on purpose. Both are bounded by the 30-minute cool-off, and both
are cheaper than the alternative.

Axes objects with **`axes.W006`** on every `check`, `migrate` and `test`. That
is answered in `SILENCED_SYSTEM_CHECKS` with the reasoning, on purpose rather
than by scrolling past it — an ignored warning stops being read, and
`check --deploy` is meant to come back with exactly one known warning.

### `BEHIND_PROXY`

Behind Caddy, `REMOTE_ADDR` is the proxy container and the real client is in
`X-Forwarded-For`. `BEHIND_PROXY=True` sets `AXES_IPWARE_PROXY_COUNT` and
`AXES_IPWARE_META_PRECEDENCE_ORDER` so the recorded address is the client's.

It does **not** change who gets locked out — that is the username — so getting
it wrong costs the audit trail, not availability. It is off by default because
there is no proxy in dev, in CI or under `runserver`, and trusting
`X-Forwarded-For` anywhere Django is directly reachable means trusting a header
the client wrote.

### Things that surprise people

- **It adds two models, so a pull that includes it needs `migrate`.** Without
  it there is no startup error and no broken page — the first **login or
  logout** returns a 500 with `relation "axes_accessattempt" does not exist`,
  because the signal handlers reach the table before anything else does.
- **The admin section is off** (`AXES_ENABLE_ADMIN = False`). Axes ships
  catalogs for ar/de/fa/fr/id/pl/ru/tr and **no Czech**, so its section and both
  its models would render English in an admin this project keeps Czech through
  three separate mechanisms. Read the log with
  `manage.py axes_list_attempts`, and clear a lockout with
  `manage.py axes_reset_username <username>`.
- **The lockout page quotes the cool-off in prose.**
  `templates/registration/lockout.html` says "přibližně za 30 minut" because the
  value in the template context is a `timedelta` that renders `0:30:00`. Change
  `AXES_COOLOFF_TIME` and the template together.
- **`Client.login()` must not appear in the test suite.** It calls
  `authenticate()` with no request, which the axes backend rejects outright. The
  suite uses `force_login` ~130 times instead, and that keeps working because
  `AxesStandaloneBackend` defines no `get_user` — so `force_login` skips it and
  picks `ModelBackend`.

## Static files

This is the fiddliest part of the configuration, because the correct behaviour
genuinely differs per environment. Three pieces interact.

### The backend is selected by `STORAGES`, not `STATICFILES_STORAGE`

Django **removed `STATICFILES_STORAGE` in 5.1**. On Django 6.1 that setting is
silently ignored — it sat in this project's settings for a while naming
WhiteNoise's manifest storage while plain storage was actually in effect, which
is exactly the kind of bug that only shows up in production.

The backend now comes from `STORAGES['staticfiles']['BACKEND']`, driven by the
`STATICFILES_BACKEND` environment variable.

To find out which backend is *really* live, check it — do not read the setting:

```python
from django.contrib.staticfiles.storage import staticfiles_storage

type(staticfiles_storage)
```

### The default is plain storage, on purpose

WhiteNoise's `CompressedManifestStaticFilesStorage` rewrites every `{% static %}`
URL to a content-hashed filename looked up in the `staticfiles.json` that
`collectstatic` writes. With no manifest on disk it **raises**:

```
ValueError: Missing staticfiles manifest entry for 'img/logo.png'
```

Setting `WHITENOISE_MANIFEST_STRICT = False` does not help — the non-strict path
falls back to hashing the file directly, and the file isn't in `STATIC_ROOT`
without a `collectstatic` run either.

Now combine that with two facts: Django's test runner **forces `DEBUG=False`**,
and `DEBUG` is precisely what short-circuits hashed-URL lookup. So making
manifest storage the global default breaks **every test that renders a
template** until someone remembers to run `collectstatic` first.

Hence the default is plain storage, and production opts in.

### Production opts in through the Dockerfile

```dockerfile
ENV STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage
RUN python manage.py collectstatic --noinput
```

Keep those two lines together. `ENV` persists into the running container, so the
backend that *writes* the manifest at build time and the one that *reads* it at
runtime are the same by construction. Setting the backend in `.env.production`
instead would reintroduce the split it prevents: an image built with plain
storage and started with manifest storage has no manifest to read and returns
500 on every page.

### Behaviour by environment

| Environment | `DEBUG` | Backend | How assets are served |
|---|---|---|---|
| `runserver` locally | `True` | plain | Django's finders, straight from `static/` |
| `manage.py test` | `False` (forced) | plain | not exercised |
| Compose dev | `True` | manifest (via image `ENV`) | hashing short-circuits on `DEBUG`; finders serve |
| Production | `False` | manifest | WhiteNoise, from `STATIC_ROOT`, hashed and compressed |

The Compose dev row is the non-obvious one: the image carries the `ENV`, but
`DEBUG=True` makes it inert. The bind-mount also shadows the image's baked
`staticfiles/`, which is harmless for the same reason.

### `static/` versus `staticfiles/`

| Directory | Role | Tracked? |
|---|---|---|
| `static/` | **Source.** `STATICFILES_DIRS`. Hand-maintained. | **Yes** |
| `staticfiles/` | **Generated.** `STATIC_ROOT`, written by `collectstatic`. | No — gitignored |

`.gitignore` used to ignore `static/`, the source directory, which is how
`static/img/background.jpg` — referenced by `static/css/app.css`, which
`base.html` loads on every page — went
missing from the repository. New assets go in `static/` and **must be
committed.**

> **Do not park non-assets under `static/`.** `collectstatic` publishes
> everything there at an unauthenticated `/static/` URL. `static/xlsx/` holds a
> working spreadsheet and is excluded in both `.gitignore` and `.dockerignore`
> for exactly that reason.

### Verifying static files

The test job uses plain storage and never requests a `/static/` URL, but CI's
`docker-build` job does: `scripts/smoke_prod_stack.sh` runs the production stack
and fetches the hashed `app.css` the login page links.

> **`static/js/job_rows.js` is not covered by that check**, and deliberately so:
> the smoke script fetches the *login* page, and the only pages linking the
> script are behind authentication. The hashed-`app.css` assertion already
> proves manifest storage is active in the image, and `.dockerignore` excludes
> only `static/xlsx/`, so `static/js/` ships — but the script is enhancement
> and a 404 on it degrades the job form to its server round-trip rather than
> breaking anything. Worth one manual `curl` after a deploy that touches it.

To check by hand before pushing, run that script in a clean worktree (see its
header), or do it step by step:

```bash
docker build -t materialinventory-test .
```

The build log should say `... static files copied to '/app/staticfiles', N
post-processed`. **A non-zero post-processed count is the proof that manifest
storage was actually active** — without it the build succeeds just as happily
with plain storage.

Then run the image with production settings and check an asset:

```bash
docker run -d --name mi-verify --network materialinventory_default \
  -e DEBUG=False -e SECRET_KEY=verify-only -e ALLOWED_HOSTS='*' \
  -e DB_HOST=db -p 18000:8000 materialinventory-test
```

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:18000/static/img/logo.png
```

Expect `200`, and expect `curl -s http://localhost:18000/login/` to contain a
**hashed** filename such as `img/logo.bef90a29a416.png`. The hash is what proves
the manifest is being read at runtime, not merely written at build time.

```bash
docker rm -f mi-verify
```

## `.dockerignore`

`COPY . .` runs before `collectstatic`, so anything the build context leaves
under `static/` gets published. `.dockerignore` keeps out `.env` (real secrets),
`.venv/`, `.git/`, the real `seed_data/*.csv`, and `static/xlsx/`, while
deliberately re-including `seed_data/*.example.csv` — the deployment runbook
copies those on the server.

## Database collation

Postgres decides its sort order when the cluster is **created**, and it cannot
be changed afterwards without a dump, a fresh volume and a restore.

`postgres:16-alpine` initialises in byte order by default, which is wrong for
Czech: `Š` sorts after `Z`. Both catalogs order by name
(`Material.Meta.ordering`, `Machine.Meta.ordering`), so this is visible in every
material dropdown on the Zpracování form and in the Materiál table — measured
against that image, the default gives

```
Olse | Olše | Struska | Zemina | Štěrk      <- byte order, wrong
Olse | Olše | Struska | Štěrk | Zemina      <- ICU cs-CZ, correct
```

`docker-compose.prod.yml` therefore passes

```yaml
POSTGRES_INITDB_ARGS: "--locale-provider=icu --icu-locale=cs-CZ --encoding=UTF8"
```

ICU collations ship in the image, so this needs no extra packages. Verify it on
a new cluster before any data goes in — `SELECT datlocprovider, daticulocale
FROM pg_database WHERE datname = current_database();` should return `i|cs-CZ`.
Step 8 of [DEPLOYMENT.md](deployment/DEPLOYMENT.md) does exactly that.

The development `docker-compose.yml` passes the same argument, but it only takes
effect on a **fresh** volume: an existing dev database keeps the collation it
was created with. If dev and production disagree about where `Štěrk` sorts, that
is why — `docker compose down -v` re-creates it (and destroys the dev data).

## Logging

`config/settings.py` defines a `LOGGING` block with one stdout handler and
`django.request` at `ERROR`.

Without it, production 500s are recorded nowhere. Django's default sends
`django.request` errors to `mail_admins` (which needs `ADMINS` and a mail
backend, neither of which this project has) and filters its console handler to
`require_debug_true`. Gunicorn only sees an ordinary response come back, so
`docker compose logs web` would stay silent while users report errors.

The level is `ERROR` and not `WARNING` on purpose: `django.request` logs every
4xx at `WARNING`, and the suite asserts a great many 403s from `role_required`.

## Locale

```python
LANGUAGE_CODE = 'cs'
TIME_ZONE = 'Europe/Prague'
USE_I18N = True
USE_TZ = True
```

`LocaleMiddleware` is **deliberately not installed**, so the language is fixed
rather than negotiated per-request from `Accept-Language`. Depot workers get
Czech regardless of their browser settings.

These settings change how numbers and dates render and how form input parses.
They are not cosmetic. See [localization.md](localization.md).

## Python version

| Where | Version |
|---|---|
| `Dockerfile` base image | 3.14 |
| CI (`setup-python`, both jobs) | 3.14 |
| Ruff `target-version` | `py312` |

The image and CI match, so the test suite runs on the interpreter production
runs. **Dependabot bumps the `Dockerfile` on its own**, so a new Python minor
arriving that way has to be copied into `ci.yml` by hand in the same pull
request.

Ruff's `target-version` stays at `py312` on purpose. It is the syntax Ruff may
*write*, not the runtime: at `py314` the formatter rewrites
`except (TypeError, ValueError):` into the unparenthesised PEP 758 form, which is
valid 3.14 and reads as Python 2.

## Other settings worth knowing

| Setting | Value | Why |
|---|---|---|
| `AUTH_USER_MODEL` | `accounts.User` | Custom user with `role`. Reference it as `settings.AUTH_USER_MODEL`, never by importing `User` into a model module. |
| `LOGIN_URL` / `LOGIN_REDIRECT_URL` | `login` / `transform_create` | Zpracování is the landing page. |
| `LOGGING` | stdout, `django.request` at `ERROR` | Without it a production 500 is logged nowhere. See above. |
| `DEFAULT_AUTO_FIELD` | `BigAutoField` | Job line items are never deleted, so the table grows indefinitely. |
