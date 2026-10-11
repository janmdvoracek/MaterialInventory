# Development

## Requirements

- Python 3.14 (the same version as the Docker image and CI).
- [uv](https://docs.astral.sh/uv/) 0.12.20 or newer (`uv self update`).
- PostgreSQL 16. **Required**: there is no SQLite fallback.
- Docker Compose (optional, but the easiest way to get Postgres).

## Setup

```bash
uv sync                               # .venv with the app plus ruff, pre-commit, coverage
source .venv/bin/activate             # or prefix each command with `uv run`
pre-commit install                    # optional
cp .env.example .env
docker compose up -d db               # just the database
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

`.env.example` sets `DB_HOST=db`, the Compose service name. To run Django on the host against Postgres in Docker, change it to `localhost`, or prefix commands with `DB_HOST=localhost`.

The full stack, `docker compose up --build`, bind-mounts the checkout, so code changes reload without a rebuild. Rebuild only when `pyproject.toml`, `uv.lock` or the `Dockerfile` changes.

## Testing

```bash
python manage.py test                                               # everything
python manage.py test --parallel                                    # one process per CPU, much faster
python manage.py test workorders                                    # one app
python manage.py test workorders.tests.test_review                  # one module
python manage.py test materials.tests.MaterialModelTests.test_material_str
```

Postgres must be reachable. A full serial run takes several minutes; `--parallel` divides that roughly by the number of cores.

**How tests are written.** Plain `django.test.TestCase`, no pytest, no mocking. Tests drive real views through `self.client` and assert on `response.context` and on the rows written. The behaviour under test *is* the database interaction, so keep that shape.

**Where tests live.** One `tests.py` per app, except `workorders`, which holds almost everything and is a `tests/` package split by topic: `test_submission`, `test_job_form`, `test_review`, `test_reports`, `test_exports`, `test_photos` and so on. The shared fixtures (`job_payload`, `default_location`, `ReviewFixtureMixin`) are in `workorders/tests/helpers.py`. A new module must be named `test_*.py`, or the runner never finds it.

Things to know:

- **There is no test settings module**, so a change to `config/settings.py` changes how the suite behaves. The locale settings are load-bearing; see [localization.md](localization.md).
- **Use `force_login`, never `Client.login()`.** See [configuration.md](configuration.md#login-rate-limiting).
- **Photo file deletion runs on `transaction.on_commit`**, which a `TestCase` never reaches. Wrap those assertions in `self.captureOnCommitCallbacks(execute=True)`. `JobPhotoTests` points `MEDIA_ROOT` at a temp directory.
- **`--parallel` clones the test database once per worker process**, and CI uses it. A test must not depend on state outside the database that another process could touch; that is why `JobPhotoTests` makes its own temporary `MEDIA_ROOT`.
- **No `TransactionTestCase`.** There are no concurrent writes. Add a counter or any read-modify-write and its test needs one.
- **Migrations were reset** to one `0001_initial` per app before production had data. A dev database from before a reset needs recreating (`docker compose down -v`, **dev file only**). From now on, model changes get ordinary migrations.

### Coverage

```bash
coverage run manage.py test --parallel   # accepts the same test labels
coverage combine                         # merge the per-process data files
coverage report -m                       # terminal table with missed lines
coverage html                            # open htmlcov/index.html
```

The configuration is in `pyproject.toml`:

- **Branch coverage is on.**
- **Excluded:** migrations, `tests.py` and `tests/`, `manage.py`, `config/asgi.py`/`wsgi.py` and `scripts/`.
- **Python only.** Templates, the scripts and the service worker don't appear.
- **Always `coverage combine` before reporting.** `concurrency = ["multiprocessing"]` and `parallel = true` make every run, parallel or not, write `.coverage.*` files. Without the combine, `report` shows the previous run's numbers, or none.
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

The configuration in `pyproject.toml` sets 120-column lines, single quotes and a `py312` target (deliberately below the runtime; see [configuration.md](configuration.md#python-version)), and excludes migrations. Beyond Ruff's defaults it enables `I` (import order), `B` (bugbear), `DJ` (Django), `UP` (pyupgrade, up to `py312`) and `SIM` (simplify). **CI runs `ruff format --check`**, which fails on unformatted code.

### Pre-commit hooks

Opt-in per clone with `pre-commit install`; CI remains the enforcement. `pre-commit run --all-files` checks everything.

| Hook | Catches |
|---|---|
| `ruff-check`, `ruff-format`, `shellcheck` | The CI `lint` job |
| `missing-migrations` | A staged `models.py` without its migration. Needs no Postgres. |
| `no-secrets-or-real-seed-data` | Committing `.env`, `.env.production` or a real `seed_data/*.csv` |
| standard checks | merge markers, large files, YAML/TOML, private keys |

- **The ruff `rev` is pinned by hand to match the `ruff` pin in `pyproject.toml`.** Dependabot bumps only `pyproject.toml` and `uv.lock`, and `pre-commit autoupdate` moves past CI's version.
- **`missing-migrations` runs `scripts/check_migrations.py`.** Git hooks get whatever `python` is first on PATH, which may have no Django. The wrapper finds `.venv` or the active virtualenv.
- **The test suite is not a hook.** It needs Postgres and takes minutes.

## Dependencies

`pyproject.toml` lists the direct dependencies, pinned exactly. `uv.lock` pins everything they pull in, with hashes. **Commit the two together.**

```bash
uv add 'somepackage==1.2.3'            # a runtime dependency; `--group dev` for a tool
uv lock --upgrade-package django       # bump one package (edit its pin first)
uv sync                                # bring .venv in line with uv.lock
```

- **Everything but your own `uv sync` is `--locked`.** The `Dockerfile` and every CI job run `uv sync --locked`, which fails instead of resolving anew when `uv.lock` is out of step with `pyproject.toml`. A pin edited by hand without `uv lock` fails CI.
- **Three sets.** The app's own dependencies are all the image gets (`--no-dev`). The `dev` group (ruff, pre-commit, coverage) comes with a plain `uv sync`. The `audit` group (pip-audit) is only for the `audit` job.
- **The project itself is never installed** (`package = false`): it is an application, not a library.
- **The image's virtualenv is `/opt/venv`**, outside `/app`, so the dev compose file's bind mount can't hide it. `PATH` puts it first, so `python` and `gunicorn` need no `uv run`. The base image's `pip` is uninstalled: nothing uses it, and its vendored libraries were all the image scan ever flagged.
- **uv's own version** is pinned by hand in three places: `required-version` in `pyproject.toml` (a floor, because an older uv may rewrite `uv.lock`), the `uv` stage in the `Dockerfile` (Dependabot bumps it), and `version:` in `ci.yml` and `security.yml`.

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

`.github/workflows/ci.yml` runs three jobs on every push to `main` and on every PR. Each installs from `uv.lock` with `uv sync --locked`.

| Job | Steps |
|---|---|
| `lint` | `ruff check`, `ruff format --check`, `shellcheck scripts/*.sh` |
| `test` | `manage.py check` → `makemigrations --check --dry-run` → `coverage run manage.py test --parallel` → `coverage combine` (Postgres 16, Python 3.14) |
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

### Security scans

`.github/workflows/security.yml` runs on the same pushes and PRs as CI, every Monday morning, and by hand.

| Job | Fails on |
|---|---|
| `audit` | a known vulnerability (pip-audit, PyPI advisory data) in a production dependency, at exactly the locked version |
| `image` | a HIGH or CRITICAL vulnerability with a fix available anywhere in the built image, OS packages included (Trivy) |

- **It never blocks a deploy.** `deploy.yml` checks `ci.yml` only, so a CVE published upstream can't stop you deploying its fix. A red run is still a to-do: bump the package (`uv lock --upgrade-package …`) or rebuild on a newer base image.
- **The weekly run** is what catches a CVE published while nothing in the repo changed.
- **Unfixed CVEs are ignored**, because there is nothing to upgrade to. Dev tools are not audited; they never reach the server.
- **Two actions are pinned to a commit**, not a tag. Trivy's tags were hijacked in March 2026, and `setup-uv` publishes no floating major tag, so `@v10` doesn't resolve. Dependabot moves the commit and its version comment together.

### Deploys and Dependabot

`.github/workflows/deploy.yml` is started by hand, from `main` only. It refuses unless CI passed for the code, then runs `scripts/deploy.sh` over SSH. See [DEPLOYMENT.md](deployment/DEPLOYMENT.md#automated-deploys).

Dependabot opens weekly PRs for uv (`pyproject.toml` with `uv.lock`), the `Dockerfile` (the Python base image and the `uv` stage) and the Actions. It doesn't update compose images or the CI Postgres service. A `python:` bump must be copied into both workflows and `requires-python` by hand, and a uv bump into both workflows.

## Conventions

- User-facing text is hardcoded Czech; don't introduce `gettext` for app copy.
- Code, comments and docs are in English.
- Comments explain *why*, not *what*. Keep them short.
- Match the surrounding code rather than importing a different style.
