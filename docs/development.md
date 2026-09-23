# Development

## Requirements

- Python 3.14 — matches the Docker image and CI; see
  [configuration.md](configuration.md#python-version).
- PostgreSQL 16 — **required**. There is no SQLite fallback; the test suite will
  not run without a reachable Postgres.
- Docker and Docker Compose (optional, but the easiest way to get the database)

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # Ruff, pinned to the CI version
cp .env.example .env
```

`.env.example` ships with `DB_HOST=db`, which is the Docker Compose service
name. **If you run Django on the host and Postgres in Docker, change it to
`localhost`** — or override per command:

```bash
DB_HOST=localhost python manage.py test
```

Start just the database:

```bash
docker compose up -d db
```

Then:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Running the app

| | Command |
|---|---|
| Dev server | `python manage.py runserver` |
| Full stack | `docker compose up --build` |
| Migrations | `python manage.py makemigrations` then `python manage.py migrate` |

The Compose stack bind-mounts the working directory, so code edits reload
without a rebuild. Rebuild only when `requirements.txt` or the `Dockerfile`
changes.

## Testing

```bash
python manage.py test                                              # everything
python manage.py test workorders                                   # one app
python manage.py test materials.tests.MaterialModelTests           # one class
python manage.py test materials.tests.MaterialModelTests.test_material_str
```

408 tests. Postgres must be reachable; expect a few minutes, and rather longer
on a Windows checkout.

### How the tests are written

One `tests.py` per app, plain `django.test.TestCase`, **no pytest and no
mocking**. Almost every test drives a real view through `self.client` and
asserts on both `response.context` and the resulting database rows. Follow that
shape — a test that mocks the ORM here would assert nothing useful, since the
behaviour under test *is* the database interaction.

There is no separate test settings module, so **a change to `config/settings.py`
is a change to how the suite behaves.** The locale settings in particular are
load-bearing (see [localization.md](localization.md)).

Two things to know about where coverage lives:

**`workorders/tests.py` is where the app is actually covered**, `StockMovement`'s
model-level tests included.

**Migrations were reset** to one `0001_initial` per app while production held no
data — twice: once when the `inventory` app went, and again when `Machine` moved
out of `materials` into its own `machines` app. A development database migrated
before a reset still records the old migrations and tables, so recreate it
(`docker compose down -v`, **dev compose file only**, then `migrate`). Once a
deployment has real rows, resetting is no longer an option: new model changes
get ordinary migrations on top.

**`EmptyLabelTests`** (`workorders/tests.py`) fails if any `ModelChoiceField`
forgets its `empty_label`. See
[localization.md](localization.md#every-modelchoicefield-needs-an-empty_label).

**`MachineRefuelTests` and `MachineRefuelReportTests`** (`workorders/tests.py`)
cover fuel: the machine row's two independent halves, the fuel-only job that is
exempt from the mass balance and from „Popis"/„Moje hodiny", the `job_edit`
round-trip that has to merge two tables back onto one row, and the Tankování card
with its own paginator and CSV. See
[architecture.md](architecture.md#a-job-that-is-only-fuel).

The suite has no `TransactionTestCase`, and nothing needs one: there is no
concurrent write path left. `StockLockConcurrencyTests` went with stock
tracking, and the last `select_for_update()` went with `Machine.total_hours`.
Every write is now an ordinary insert or delete whose result does not depend on
what another request is doing. If you add a counter or any other read-modify-write,
it needs a `TransactionTestCase` again — a plain `TestCase` wraps each test in a
single transaction and hides the interleaving such a test exists to exercise.

**`AdminCzechTests`** (in `accounts/tests.py`) fails if the admin drifts back
to English — including if `locale/cs/LC_MESSAGES/django.mo` is stale. See
[localization.md](localization.md#the-admin).

**`AdminGateTests`** (in `accounts/tests.py`) fails if `/admin/` becomes
reachable without an admin session — anonymously, as a worker, or as an
`is_staff` manager — and `AdminLinkTests` fails if the header link stops
agreeing with that gate. The deployment is public, so this is the difference
between a superuser login form on the internet and none at all; see
[architecture.md](architecture.md#the-admin-is-a-404-unless-you-are-already-an-admin).
`scripts/smoke_prod_stack.sh` re-checks the same 404 through the built image
behind Caddy, which is the only place middleware order is exercised for real.

## Translations

Only Django's own untranslated strings live in a catalog; app copy is hardcoded
Czech. After editing `locale/cs/LC_MESSAGES/django.po`, recompile — the runtime
reads the `.mo`, and both files are committed:

```bash
python manage.py compilemessages -l cs --ignore=.venv
```

Requires GNU gettext (`msgfmt`) locally. The Docker image does not need it,
because the compiled `.mo` ships in the repo rather than being built.

## Linting

```bash
ruff check .      # lint
ruff format .     # format
```

Config lives in `pyproject.toml`: 120-column lines, single quotes, Python 3.12
target (deliberately below the 3.14 runtime; see
[configuration.md](configuration.md#python-version)), migrations excluded. Three rules are disabled with rationale in the
file — Django's `class Meta` mutables, quoted `Decimal()` literals, and a
naive-datetime rule that only fires in test helpers.

**CI runs `ruff format --check`**, which fails on unformatted code rather than
fixing it. Run `ruff format .` before pushing.

## Seeding data

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
cp seed_data/machines.example.csv seed_data/machines.csv
cp seed_data/locations.example.csv seed_data/locations.csv
cp seed_data/users.example.csv seed_data/users.csv
# edit, then:
python manage.py seed_data
```

Real `seed_data/*.csv` files are gitignored; only the `.example.csv` templates
are committed. Each file path can be overridden, e.g.
`--materials-file=path/to/other.csv`.

### Columns

| File | Columns |
|---|---|
| `materials.csv` | `sku`, `name` |
| `machines.csv` | `name`, `hourly_rate` *(optional)*, `rate_per_ton` *(optional)* |
| `locations.csv` | `name` |
| `users.csv` | `username`, `first_name`, `last_name`, `email`, `role` |

`sku` and `name` are the match keys, so re-running updates in place rather than
duplicating. The whole command is one transaction.

A row **shorter than the header is fine** — `Bagr,10` under
`name,hourly_rate,rate_per_ton` is read as an unset `rate_per_ton`, exactly like
a blank cell. `csv.DictReader` hands back `None` rather than `''` for a column
the row never reached, which used to crash the command with an
`AttributeError`; trailing commas are easy to leave off by hand and a
spreadsheet export drops them, so the real `machines.csv` hit it.

A **missing required column** (`sku`/`name` for materials, `name` for machines,
`username` for users) aborts with a `CommandError` naming the file and the
column. Without that check a mistyped header skips every row and reports
`0 created`, which reads as "already up to date" rather than "this file is
wrong". Extra columns are still ignored, so an older CSV carrying retired ones
keeps working.

**Optional columns are only written when the cell actually holds a value.**
Omitting the column, or leaving the cell blank, *preserves* what is already in
the database — re-seeding never clobbers a rate or flag someone set by hand in
the admin. New records fall back to the model default (`hourly_rate=NULL`,
`rate_per_ton=NULL`).
Clearing a value back to empty is an admin action, not a CSV one.

**Locations are create-only.** A name already in the database is left exactly
as it is — there is nothing else to update, and in particular `is_active` is not
touched, so a location retired in the admin is not revived by a re-seed. The
committed `locations.example.csv` is placeholders; replace them with the real
sites.

A machine has no stored hours to seed. Its motohodiny and tonnage on Stroje are
summed from the approved usage rows every time the page is rendered.

### Users

- `role` must be `WORKER`, `MANAGER`, or `ADMIN`. An invalid role aborts the
  whole command.
- Existing usernames are **skipped**, never updated. Seeding cannot change or
  reset an existing account.
- Each new account gets a random temporary password printed to the console.
  Share it securely; the user changes it at *Změnit heslo*.
- Only `ADMIN` accounts receive Django admin access.

## CI

`.github/workflows/ci.yml` runs three independent jobs on every push to `main`
and every pull request:

| Job | Steps |
|---|---|
| `lint` | `ruff check .`, then `ruff format --check .`, then `shellcheck scripts/*.sh` |
| `docker-build` | `scripts/smoke_prod_stack.sh` — builds the image and runs the whole production stack (see below) |
| `test` | `manage.py check` → `manage.py makemigrations --check --dry-run` → `manage.py test` against a Postgres 16 service, on Python 3.14 like the image |

The middle step of the test job is the one that surprises people: **a model
change without its migration file fails CI even when every test passes.** Run
`makemigrations` and commit the result alongside any `models.py` edit.

**Documentation-only changes skip CI entirely.** Both triggers carry a
`paths-ignore` list, and since `30c6477` it holds a single pattern: `'**.md'`.
A commit touching nothing but Markdown runs **no jobs at all**; a commit
touching even one other file runs the full pipeline.

Dotfiles and dot-directories used to be exempt too, via four extra globs. Those
were removed, so `.gitignore`, `.dockerignore`, `.env.example`, and everything
under `.github/` trigger CI like any other file — which is the safer default,
since `.dockerignore` decides what lands in the image and `ci.yml` *is* the
pipeline.

> **Watch out if `main` has branch protection** with these jobs as *required
> status checks*. A skipped workflow reports nothing rather than success, so a
> docs-only pull request would sit unmergeable, waiting for a check that will
> never arrive. The standard fix is a second workflow, triggered on `**.md`
> only, with jobs of the same names that do nothing and pass. The same gap is
> why the deploy workflow cannot simply ask whether CI passed for the commit it
> deploys — see below.

### The production smoke test

The `docker-build` job keeps its name but no longer stops at `docker build`.
`scripts/smoke_prod_stack.sh` brings up `docker-compose.prod.yml` — Postgres,
gunicorn *and* Caddy — under its own project name. It uses an `.env.production`
generated from `.env.production.example`, with only the placeholders filled in
and the domain set to `localhost`, for which Caddy issues its own certificate.
Then it checks:

| Check | The failure it stands for |
|---|---|
| `compose config`, `caddy validate` | A typo in the compose file or the `Caddyfile` |
| Login page links a hashed `app.css`, and it is served | Manifest storage not active: a 500 on every page in production |
| `check --deploy` reports exactly `security.W021` | Runbook step 12. Also fails if **`.env.production.example` changes** in a way that adds a warning |
| `http://` redirects to `https://` | Caddy's automatic HTTPS not in effect |
| `https://…/login/` answers 200 with HSTS | A 301 means Django no longer sees the request as HTTPS: Caddy's `X-Forwarded-Proto` or `SECURE_PROXY_SSL_HEADER` |
| A login POST carrying `Origin:` gets a 302, and the session works | A 403 is the CSRF origin check failing: the proxy lost the `Host` or the scheme |

It does **not** catch a removed `FORWARDED_ALLOW_IPS` or an empty
`CSRF_TRUSTED_ORIGINS`. Both were tried against this stack, and neither made a
difference. Gunicorn 26 passes `X-Forwarded-Proto` through to Django from any
peer; its allow-list only gates gunicorn's own `wsgi.url_scheme`. And once Django
sees HTTPS, `Origin: https://<host>` already matches the request's own host.

It writes `.env.production` into the checkout it runs in and **refuses to start
if one already exists**, so it cannot clobber real settings. To run it locally,
use a clean worktree; it needs Docker and free ports 80 and 443:

```bash
git worktree add ../mi-smoke && ../mi-smoke/scripts/smoke_prod_stack.sh
```

On Windows under Git Bash, prefix it with `MSYS2_ARG_CONV_EXCL=/etc/caddy`, or
the in-container Caddyfile path gets rewritten into a Windows one. Not
`MSYS_NO_PATHCONV=1`: that also stops `/dev/null` and the temp paths being
translated for curl, and every request fails with curl error 23.

It checks one static file, not all of them; for a change to other assets see
[configuration.md](configuration.md#verifying-static-files).

### Deploys

`.github/workflows/deploy.yml` deploys to the production VPS, **started by hand**
from the Actions tab and only from `main`. It refuses unless CI passed for the
code, then runs `scripts/deploy.sh` on the server over SSH. How it decides what
is tested, and the one-time server and GitHub setup, are in
[DEPLOYMENT.md](deployment/DEPLOYMENT.md#automated-deploys).

Dependabot (`.github/dependabot.yml`) opens weekly update pull requests for pip
packages, the `Dockerfile` base image, and the Actions themselves. It does not
update the images named in the compose files or in `ci.yml`'s Postgres service.
A `python:` bump in the `Dockerfile` has to be matched by hand in both
`setup-python` steps of `ci.yml`.

## Conventions

- **User-facing text is hardcoded Czech**, not `gettext`. Do not introduce
  `{% trans %}` half-way; see [localization.md](localization.md).
- Code, comments, docstrings, and these docs are English.
- Comments explain *why*, not *what*. The existing ones are load-bearing —
  several record a decision that looks wrong until you know the reason.
- Match the surrounding code's density and idiom rather than importing a
  different house style.
