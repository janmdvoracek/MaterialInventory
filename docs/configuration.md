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
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated. In production this is the server's LAN IP. |
| `CSRF_TRUSTED_ORIGINS` | empty | Comma-separated, **each entry with a scheme** (`https://depot.example.com`); a leading wildcard is allowed (`https://*.loca.lt`). Leave empty for plain HTTP — see below. |
| `DB_NAME` | `materialinventory` | |
| `DB_USER` | `materialinventory` | |
| `DB_PASSWORD` | `materialinventory` | Change in production. |
| `DB_HOST` | `localhost` | `.env.example` sets `db` for Compose. Use `localhost` when running Django on the host. |
| `DB_PORT` | `5432` | |
| `STATICFILES_BACKEND` | plain `StaticFilesStorage` | Set by the `Dockerfile`. See below. |

`.env` is gitignored; `.env.example` is the committed template.

## `CSRF_TRUSTED_ORIGINS` and TLS in front of Django

Empty is correct for both supported setups — local `runserver` and the planned
LAN deployment — because both are plain HTTP end to end. Django then compares a
request's `Origin` header against its own host, and the `ALLOWED_HOSTS` entry is
all the configuration CSRF needs.

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

Do **not** reach for `SECURE_PROXY_SSL_HEADER` instead. It makes Django trust an
`X-Forwarded-Proto` header, which is only safe behind a proxy that overwrites
that header on every request; with `runserver` exposed through a tunnel anyone
can forge it.

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

CI does not cover this: the test job uses plain storage and never requests a
`/static/` URL, and `docker build` never starts the container. Check by hand
after any static-file change.

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
| CI (`setup-python`) | 3.12 |
| Ruff `target-version` | `py312` |

These currently disagree. It works — nothing in the codebase is version
sensitive — but the container runs a different interpreter from the one the test
suite is verified against, so a 3.13/3.14 behaviour change would first appear in
production. Worth aligning.

## Other settings worth knowing

| Setting | Value | Why |
|---|---|---|
| `AUTH_USER_MODEL` | `accounts.User` | Custom user with `role`. Reference it as `settings.AUTH_USER_MODEL`, never by importing `User` into a model module. |
| `LOGIN_URL` / `LOGIN_REDIRECT_URL` | `login` / `transform_create` | Zpracování is the landing page. |
| `REST_FRAMEWORK` | session auth, `IsAuthenticated` | Configured but unused — there are no API routes. |
| `DEFAULT_AUTO_FIELD` | `BigAutoField` | Job line items are never deleted, so the table grows indefinitely. |
