# Configuration

Settings live in `config/settings.py` and read the environment through [python-decouple](https://github.com/HBNetwork/python-decouple). Every setting has a working default, so a bare checkout starts with no `.env`, and `collectstatic` can run at Docker build time.

**Precedence:** real environment variables, then `.env`, then the default. That is why the `Dockerfile`'s `ENV` lines win over a mounted `.env`.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | insecure literal | **Set in production.** Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`. |
| `DEBUG` | `True` | `False` in production. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Host names, no scheme. |
| `CSRF_TRUSTED_ORIGINS` | empty | Origins **with scheme** (`https://inventar.firma.cz`). Required behind TLS termination; see below. |
| `SECURE_SSL_REDIRECT` | `False` | Redirect HTTP to HTTPS. |
| `SECURE_COOKIES` | `False` | Sets both `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`. |
| `SECURE_HSTS_SECONDS` | `0` | Hard to undo; see [HSTS](#hsts). |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `False` | Safe on a subdomain deployment. |
| `SECURE_HSTS_PRELOAD` | `False` | Left off, so `check --deploy` reports exactly `W021`. |
| `BEHIND_PROXY` | `False` | django-axes reads the client IP from `X-Forwarded-For`. |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | `materialinventory` | Change the password in production. |
| `DB_HOST` | `localhost` | `.env.example` sets `db` for Compose. |
| `DB_PORT` | `5432` | |
| `STATICFILES_BACKEND` | plain `StaticFilesStorage` | Set by the `Dockerfile`; see [Static files](#static-files). |

`.env` is gitignored; `.env.example` is the template.

### Read by Compose and gunicorn, not Django

These live in `.env.production`, made from `.env.production.example`. The real file is gitignored and kept out of the image.

| Variable | Notes |
|---|---|
| `APP_DOMAIN` | The host Caddy gets a certificate for. Its DNS record must point here before the first `up`. |
| `ACME_EMAIL` | The Let's Encrypt account email; renewal warnings go here. |
| `WEB_CONCURRENCY` | Gunicorn worker count. Its default of 1 serialises requests; production uses 3. |
| `FORWARDED_ALLOW_IPS` | Set to `*` in `docker-compose.prod.yml` itself, not in the env file. See below. |

**Every prod compose command needs `--env-file .env.production`.** A service's `env_file:` fills the *container's* environment, but `${...}` interpolation in the compose file reads only the shell and `.env`. Without the flag, the `:?` guards stop the stack with a named error. Also, under Compose an edited env file needs `up -d` to recreate the container; `restart` keeps the old values.

## HTTPS behind the proxy

Production runs Caddy in front of gunicorn. Caddy was chosen over nginx+certbot because it issues *and renews* certificates by itself; the whole config is the short `Caddyfile`. Caddy terminates TLS and talks plain HTTP to gunicorn over the Compose network. Four pieces have to agree.

**1. The HTTPS settings default to off.** The test runner forces `DEBUG=False`, so a hardcoded `SECURE_SSL_REDIRECT = True` would turn every test request into a 301. Production opts in through `.env.production`.

**2. `SECURE_PROXY_SSL_HEADER` is set unconditionally.** It tells Django to trust `X-Forwarded-Proto`. That is safe only because:

- `web` publishes **no host port** (`expose:`, not `ports:`), so only Caddy can reach gunicorn; and
- Caddy overwrites `X-Forwarded-*` on every request.

Re-adding a `ports:` line to `web` breaks this. For the same reason, don't use this setup on a `runserver` reached through a tunnel.

**3. `FORWARDED_ALLOW_IPS: "*"` on `web`.** Gunicorn ignores `X-Forwarded-*` from peers outside this list, and its default (`127.0.0.1`) doesn't include the Caddy container. Without it, Django never sees HTTPS, so `SECURE_SSL_REDIRECT` redirects forever. It looks like a proxy bug, but the fix is here. `*` is safe only because of point 2.

**4. `CSRF_TRUSTED_ORIGINS=https://<domain>`.** The browser sends `Origin: https://…` while Django, reached over HTTP, expects `http://…`. Every POST fails with 403 "Origin checking failed", login included, while pages load fine. `ALLOWED_HOSTS` plays no part in this check, and `SECURE_PROXY_SSL_HEADER` is not a substitute.

The same applies to a dev tunnel (localtunnel, ngrok, Cloudflare Tunnel). For localtunnel, use wildcards, since the subdomain changes on every run:

```bash
ALLOWED_HOSTS=localhost,127.0.0.1,.loca.lt
CSRF_TRUSTED_ORIGINS=https://*.loca.lt
```

> The CI smoke test cannot catch a missing `FORWARDED_ALLOW_IPS` or an empty `CSRF_TRUSTED_ORIGINS`. With gunicorn 26 and the stack as built, both happen to pass anyway. They are still required: they are what makes the setup correct rather than accidentally working.

### HSTS

A browser that has seen the HSTS header refuses plain HTTP for that host until the max-age expires, whatever the server later says. Start at `3600`, and raise it to `31536000` after a certificate renewal has actually happened.

`SECURE_HSTS_INCLUDE_SUBDOMAINS` is safe on a subdomain like `inventar.firma.cz`. At the domain apex it would commit every company subdomain to HTTPS.

`SECURE_HSTS_PRELOAD` stays off. Removing a domain from a browser's preload list takes months.

## Login rate limiting

`django-axes`: **five failed logins lock the account for 30 minutes**, and a successful login clears the counter.

**It locks the username, not the IP address** (`AXES_LOCKOUT_PARAMETERS = ['username']`). Staff share a few egress addresses (the office NAT, mobile carriers), and behind the proxy unknown requests look like the proxy container. Locking by IP would let one worker's typo lock out the whole depot. The trade-off: an attacker gets five guesses per account per cool-off from any number of addresses, and can lock a known user out for 30 minutes.

- **`axes.W006`** (axes objecting to username locking) is answered in `SILENCED_SYSTEM_CHECKS`, so `check --deploy` still reports exactly one warning.
- **`BEHIND_PROXY=True`** makes axes record the real client IP rather than the proxy. That matters only for the audit trail, not for who gets locked out. It is off by default because trusting `X-Forwarded-For` where Django is directly reachable means trusting the client.
- **Axes adds two models, so it needs `migrate`.** Without them, the first login or logout returns a 500 with `relation "axes_accessattempt" does not exist`.
- **The axes admin section is off** (`AXES_ENABLE_ADMIN = False`), because axes has no Czech translation. Use `manage.py axes_list_attempts` and `axes_reset_username <name>` instead.
- **`lockout.html` says "about 30 minutes" in prose**, so change it together with `AXES_COOLOFF_TIME`.
- **Tests use `force_login`, never `Client.login()`.** The latter calls `authenticate()` without a request, which axes rejects. `force_login` works because `AxesStandaloneBackend` defines no `get_user`.

## Static files

**The backend comes from `STORAGES['staticfiles']`**, driven by `STATICFILES_BACKEND`. (`STATICFILES_STORAGE` was removed in Django 5.1 and is silently ignored.) To see which backend is live, check it rather than reading the setting:

```python
from django.contrib.staticfiles.storage import staticfiles_storage

type(staticfiles_storage)
```

**The default is plain storage, so the tests work.** WhiteNoise's manifest storage rewrites every `{% static %}` URL to a hashed name from `staticfiles.json`, and raises `Missing staticfiles manifest entry` when there is no manifest. The test runner forces `DEBUG=False`, which is exactly when that lookup happens. A manifest default would therefore break every test that renders a template.

**Production opts in through the `Dockerfile`:**

```dockerfile
ENV STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage
RUN python manage.py collectstatic --noinput
```

Keep those two lines together. The `ENV` persists into the container, so the backend that writes the manifest is the one that reads it. Setting it in `.env.production` instead could start an image without a manifest, giving a 500 on every page.

| Environment | `DEBUG` | Backend | Served by |
|---|---|---|---|
| `runserver` | `True` | plain | Django's finders |
| `manage.py test` | `False` (forced) | plain | not exercised |
| Compose dev | `True` | plain (`docker-compose.yml` overrides the image's `ENV`) | Django's finders |
| Production | `False` | manifest | WhiteNoise, hashed and compressed |

**`static/` is source** (tracked; commit new assets). **`staticfiles/` is generated** (gitignored). `collectstatic` publishes everything under `static/` at an unauthenticated URL, so never put non-assets there. `static/xlsx/` is excluded in both `.gitignore` and `.dockerignore` for that reason.

**Verifying.** CI's `docker-build` job runs `scripts/smoke_prod_stack.sh`, which checks two things:

- the login page links a hashed `app.css` that is served;
- the manifest names a hashed `icon-512.png` that is served.

It doesn't fetch the job-form scripts, which sit behind login. Since they are enhancement, a 404 there only degrades the form, but curl them after a deploy that changes them. When building by hand, a non-zero "post-processed" count in the `collectstatic` output proves manifest storage was active.

## Uploaded files (`MEDIA_ROOT`)

Job photos are the mirror image of static files:

| | `STATIC_ROOT` | `MEDIA_ROOT` |
|---|---|---|
| Contents | assets in the repo | photos workers upload |
| Written by | `collectstatic`, at build time | the app, at runtime |
| Served by | WhiteNoise, unauthenticated | `protected_media`, manager only |
| In production | baked into the image | the `media_data` volume |
| Backed up by | nothing needed (rebuilt) | **nothing yet** |

`MEDIA_ROOT` is always `BASE_DIR/media` and `MEDIA_URL` is `media/`. The safety rests on nothing but Django serving the directory. **Pointing Caddy at it, or adding it to `STATICFILES_DIRS`, publishes every photo.** The smoke script checks that a `media/` URL redirects an anonymous visitor to login and gives a worker a 403.

- **In production it must be a volume.** Otherwise every deploy discards the photos, and `down -v` now destroys them.
- **`scripts/backup_db.sh` doesn't include photos.** The runbook shows how to tar the volume.
- **The upload caps** live in `workorders/forms.py`: 10 MB per photo, 20 MB per submission, 10 per job. Stored files are shrunk to a few hundred KB, so the volume grows slowly. The `Caddyfile`'s `request_body max_size 25MB` sits above the form's caps as a backstop.

## `.dockerignore`

`COPY . .` runs before `collectstatic`, so anything left under `static/` gets published. `.dockerignore` excludes:

- `.env`
- `.venv/`
- `.git/`
- the real `seed_data/*.csv` (the `*.example.csv` files stay in)
- `static/xlsx/`
- `media/`

## Database collation

Postgres fixes its sort order when the cluster is created. Changing it later means dump, recreate, restore. The default `postgres:16-alpine` sorts byte-wise, which puts `Š` after `Z`. Both catalogs order by name, so this shows in every dropdown:

```
Olse | Olše | Struska | Zemina | Štěrk      <- byte order (wrong)
Olse | Olše | Struska | Štěrk | Zemina      <- ICU cs-CZ
```

Both compose files pass `POSTGRES_INITDB_ARGS: "--locale-provider=icu --icu-locale=cs-CZ --encoding=UTF8"`, which is read only on an empty volume. Step 8 of the [runbook](deployment/DEPLOYMENT.md) verifies it before any data exists. An old dev volume keeps byte order until it is recreated.

## Logging

`LOGGING` sends `django.request` to stdout at `ERROR`. Without it, production 500s are recorded nowhere: Django's default mails them to `ADMINS` (there are none) and its console handler only runs under `DEBUG`. The level is `ERROR` rather than `WARNING` because `WARNING` would also log every 403 the test suite triggers.

## Locale

`LANGUAGE_CODE = 'cs'`, `TIME_ZONE = 'Europe/Prague'`, with `USE_I18N` and `USE_TZ` on. `LocaleMiddleware` is deliberately not installed: workers get Czech whatever their browser says. The consequences for numbers and dates are real; see [localization.md](localization.md).

## Python version

The `Dockerfile` and CI both use Python 3.14. Dependabot bumps only the `Dockerfile`, so copy a new minor version into `ci.yml` by hand.

Ruff's `target-version` stays at `py312` on purpose. It controls the syntax Ruff *writes*: at `py314`, the formatter rewrites `except (A, B):` into the unparenthesised PEP 758 form.

## Other settings

| Setting | Value | Why |
|---|---|---|
| `AUTH_USER_MODEL` | `accounts.User` | Custom user with `role`. Reference it as `settings.AUTH_USER_MODEL` in models. |
| `LOGIN_URL` / `LOGIN_REDIRECT_URL` | `login` / `transform_create` | Zpracování is the landing page. |
| `DEFAULT_AUTO_FIELD` | `BigAutoField` | Line item tables only grow. |
