# Development

## Requirements

- Python 3.14 (the same version as the Docker image and CI).
- PostgreSQL 16. **Required**: there is no SQLite fallback.
- Docker Compose (optional, but the easiest way to get Postgres).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # ruff, pre-commit, coverage
pre-commit install                    # optional
cp .env.example .env
docker compose up -d db               # just the database
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

`.env.example` sets `DB_HOST=db`, the Compose service name. To run Django on the host against Postgres in Docker, change it to `localhost`, or prefix commands with `DB_HOST=localhost`.

The full stack, `docker compose up --build`, bind-mounts the checkout, so code changes reload without a rebuild. Rebuild only when `requirements.txt` or the `Dockerfile` changes.

## Testing

```bash
python manage.py test                                               # everything
python manage.py test workorders                                    # one app
python manage.py test materials.tests.MaterialModelTests.test_material_str
```

Postgres must be reachable. A full run takes a few minutes.

**How tests are written.** One `tests.py` per app, plain `django.test.TestCase`, no pytest, no mocking. Tests drive real views through `self.client` and assert on `response.context` and on the rows written. The behaviour under test *is* the database interaction, so keep that shape. `workorders/tests.py` holds almost everything.

Things to know:

- **There is no test settings module**, so a change to `config/settings.py` changes how the suite behaves. The locale settings are load-bearing; see [localization.md](localization.md).
- **Use `force_login`, never `Client.login()`.** See [configuration.md](configuration.md#login-rate-limiting).
- **Photo file deletion runs on `transaction.on_commit`**, which a `TestCase` never reaches. Wrap those assertions in `self.captureOnCommitCallbacks(execute=True)`. `JobPhotoTests` points `MEDIA_ROOT` at a temp directory.
- **No `TransactionTestCase`.** There are no concurrent writes. Add a counter or any read-modify-write and its test needs one.
- **Migrations were reset** to one `0001_initial` per app before production had data. A dev database from before a reset needs recreating (`docker compose down -v`, **dev file only**). From now on, model changes get ordinary migrations.

### Coverage

```bash
coverage run manage.py test      # accepts the same test labels
coverage report -m               # terminal table with missed lines
coverage html                    # open htmlcov/index.html
```

The configuration is in `pyproject.toml`:

- **Branch coverage is on.**
- **Excluded:** migrations, `tests.py`, `manage.py`, `config/asgi.py`/`wsgi.py` and `scripts/`.
- **Python only.** Templates, the scripts and the service worker don't appear.
- **Don't add `--parallel`** without configuring `concurrency = ["multiprocessing"]` and running `coverage combine`.
- **Only a full run gives meaningful numbers.** A single-app run still lists every file.

## Translations

App copy is hardcoded Czech. Only Django's own untranslated strings live in `locale/cs/LC_MESSAGES/django.po`. After editing it, recompile; both `.po` and `.mo` are committed, and the runtime reads the `.mo`:

```bash
python manage.py compilemessages -l cs --ignore=.venv
```

This needs GNU gettext (`msgfmt`) locally. The Docker image doesn't, because it ships the committed `.mo`.

## Linting

```bash
ruff check .
ruff format .
```

The configuration in `pyproject.toml` sets 120-column lines, single quotes and a `py312` target (deliberately below the runtime; see [configuration.md](configuration.md#python-version)), and excludes migrations. **CI runs `ruff format --check`**, which fails on unformatted code.

### Pre-commit hooks

Opt-in per clone with `pre-commit install`; CI remains the enforcement. `pre-commit run --all-files` checks everything.

| Hook | Catches |
|---|---|
| `ruff-check`, `ruff-format`, `shellcheck` | The CI `lint` job |
| `missing-migrations` | A staged `models.py` without its migration. Needs no Postgres. |
| `no-secrets-or-real-seed-data` | Committing `.env`, `.env.production` or a real `seed_data/*.csv` |
| standard checks | merge markers, large files, YAML/TOML, private keys |

- **The ruff `rev` is pinned by hand to match `requirements-dev.txt`.** Dependabot bumps only the requirements file, and `pre-commit autoupdate` moves past CI's version.
- **`missing-migrations` runs `scripts/check_migrations.py`.** Git hooks get whatever `python` is first on PATH, which may have no Django. The wrapper finds `.venv` or the active virtualenv.
- **The test suite is not a hook.** It needs Postgres and takes minutes.

## Seeding data

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
cp seed_data/machines.example.csv seed_data/machines.csv
cp seed_data/locations.example.csv seed_data/locations.csv
cp seed_data/users.example.csv seed_data/users.csv
# edit, then:
python manage.py seed_data
```

The real CSVs are gitignored. Each path can be overridden (`--materials-file=…`). The whole command runs in one transaction.

| File | Columns | Matched on | Behaviour |
|---|---|---|---|
| `materials.csv` | `sku`, `name` | `sku` | updates in place |
| `machines.csv` | `name`, `hourly_rate`?, `rate_per_ton`? | `name` | a blank or missing optional cell **keeps** the stored value |
| `locations.csv` | `name` | `name` | create-only; never touches an existing one, including `is_active` |
| `users.csv` | `username`, `first_name`, `last_name`, `email`, `role` | `username` | create-only; existing users are skipped |

- **Short rows are fine.** `Bagr,10` under a three-column header leaves `rate_per_ton` unset.
- **A missing required column** aborts with a `CommandError` naming it, rather than skipping every row and reporting "0 created". Extra columns are ignored.
- **Clearing a rate** is done in the admin, not through the CSV.
- **`role`** must be `WORKER`, `MANAGER` or `ADMIN`. Each new user gets a random temporary password printed to the console. Only `ADMIN` gets admin access.
- **The example files differ.** `locations.example.csv` and `users.example.csv` are placeholders; the materials and machines examples carry the real catalog.

## CI

`.github/workflows/ci.yml` runs three jobs on every push to `main` and on every PR:

| Job | Steps |
|---|---|
| `lint` | `ruff check`, `ruff format --check`, `shellcheck scripts/*.sh` |
| `test` | `manage.py check` → `makemigrations --check --dry-run` → `coverage run manage.py test` (Postgres 16, Python 3.14) |
| `docker-build` | `scripts/smoke_prod_stack.sh` (below) |

- **A model change without its migration fails CI**, even if every test passes.
- **Coverage is reported, not enforced.** A passing `test` job writes the table to the run summary and uploads `htmlcov/` as the `coverage-html` artifact (14 days).
- **Markdown-only commits skip CI entirely** (`paths-ignore: '**.md'`). Every other file triggers the full pipeline. If `main` gets required status checks, a docs-only PR would wait forever for checks that never run; the fix is a stub workflow with the same job names.

### The production smoke test

`scripts/smoke_prod_stack.sh` brings up all of `docker-compose.prod.yml` (Postgres, gunicorn, Caddy) under a throwaway project name. It generates an `.env.production` from the example, with domain `localhost`.

| Check | Failure it catches |
|---|---|
| `compose config`, `caddy validate` | a broken compose file or `Caddyfile` |
| the login page links a hashed `app.css`, which is served | manifest storage not active (500 on every page) |
| the manifest names a hashed `icon-512.png`; `/sw.js` is JavaScript | a broken PWA install |
| `check --deploy` reports exactly `W021` | a settings regression, including in `.env.production.example` |
| `http://` redirects to `https://` | Caddy's automatic HTTPS off |
| HTTPS answers 200 with HSTS | a 301 means Django doesn't see HTTPS |
| a login POST with `Origin:` gets a 302 | a 403 means the CSRF origin check failed |
| `/admin` and `/admin/` are 404 for anonymous visitors and workers | the admin gate missing or moved below `CommonMiddleware` |
| `media/` redirects anonymous visitors, 403 for a worker | photos published |

It **refuses to run if `.env.production` already exists**. Run it in a clean worktree, with Docker and ports 80/443 free:

```bash
git worktree add ../mi-smoke && ../mi-smoke/scripts/smoke_prod_stack.sh
```

On Windows Git Bash, prefix it with `MSYS2_ARG_CONV_EXCL=/etc/caddy`. Don't use `MSYS_NO_PATHCONV=1`, which breaks curl's temp paths.

### Deploys and Dependabot

`.github/workflows/deploy.yml` is started by hand, from `main` only. It refuses unless CI passed for the code, then runs `scripts/deploy.sh` over SSH. See [DEPLOYMENT.md](deployment/DEPLOYMENT.md#automated-deploys).

Dependabot opens weekly PRs for pip, the `Dockerfile` base image and the Actions. It doesn't update compose images or the CI Postgres service. A `python:` bump must be copied into `ci.yml` by hand.

## Conventions

- User-facing text is hardcoded Czech; don't introduce `gettext` for app copy.
- Code, comments and docs are in English.
- Comments explain *why*, not *what*. Keep them short.
- Match the surrounding code rather than importing a different style.
