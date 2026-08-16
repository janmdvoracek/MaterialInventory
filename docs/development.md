# Development

## Requirements

- Python 3.12 — matches CI and Ruff's `target-version`. Note the Docker image
  builds on 3.14; see [configuration.md](configuration.md#python-version).
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
python manage.py test inventory                                    # one app
python manage.py test materials.tests.MaterialModelTests           # one class
python manage.py test materials.tests.MaterialModelTests.test_material_str
```

171 tests, roughly two minutes. Postgres must be reachable.

### How the tests are written

One `tests.py` per app, plain `django.test.TestCase`, **no pytest and no
mocking**. Almost every test drives a real view through `self.client` and
asserts on both `response.context` and the resulting database rows. Follow that
shape — a test that mocks the ORM here would assert nothing useful, since the
behaviour under test *is* the database interaction.

There is no separate test settings module, so **a change to `config/settings.py`
is a change to how the suite behaves.** The locale settings in particular are
load-bearing (see [localization.md](localization.md)).

Three departures from the standard shape, each for a reason:

**`StockLockConcurrencyTests`** (`inventory/tests.py`) is a
`TransactionTestCase`, not a `TestCase`. `TestCase` wraps each test in a single
transaction, which would hide the very interleaving these tests exist to
exercise. Consequences when editing them:

- Worker threads must `connection.close()` in a `finally` block.
- Thread exceptions are collected into a list and re-asserted on the main
  thread. A thread that dies silently would otherwise become a *passing* test —
  hence the `# noqa: BLE001` on the broad except.
- They are slower and do not share the fixture setup.

**`DashboardTemplateTests` / `MovementHistoryTemplateTests`** assert on rendered
HTML rather than context, covering what context assertions structurally cannot:
the Czech comma decimal separator, local-time timestamps, and negative-stock
styling. When matching a CSS class, match `class="qty-negative"` and not the
bare string — `base.html` ships a `td.qty-negative` rule on every page, so the
bare name is always present and the assertion would pass vacuously.

**`EmptyLabelTests`** (in both `inventory/tests.py` and `workorders/tests.py`)
fails if any `ModelChoiceField` forgets its `empty_label`. See
[localization.md](localization.md#every-modelchoicefield-needs-an-empty_label).

## Linting

```bash
ruff check .      # lint
ruff format .     # format
```

Config lives in `pyproject.toml`: 120-column lines, single quotes, Python 3.12
target, migrations excluded. Three rules are disabled with rationale in the
file — Django's `class Meta` mutables, quoted `Decimal()` literals, and a
naive-datetime rule that only fires in test helpers.

**CI runs `ruff format --check`**, which fails on unformatted code rather than
fixing it. Run `ruff format .` before pushing.

## Seeding data

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
cp seed_data/locations.example.csv seed_data/locations.csv
cp seed_data/machines.example.csv seed_data/machines.csv
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
| `materials.csv` | `sku`, `name`, `unit_of_measure`, `category`, `track_stock` *(optional)* |
| `locations.csv` | `name` |
| `machines.csv` | `name`, `hourly_rate` *(optional)* |
| `users.csv` | `username`, `first_name`, `last_name`, `email`, `role` |

`sku` and `name` are the match keys, so re-running updates in place rather than
duplicating. The whole command is one transaction.

**Optional columns are only written when the cell actually holds a value.**
Omitting the column, or leaving the cell blank, *preserves* what is already in
the database — re-seeding never clobbers a rate or flag someone set by hand in
the admin. New records fall back to the model defaults (`track_stock=True`,
`hourly_rate=NULL`). Clearing a value back to empty is an admin action, not a
CSV one.

`track_stock` accepts `true`/`false` (also `0`, `no`, `ne`). It should be
**false** for materials the depot consumes but never formally receives, such as
on-site excavated soil — see
[architecture.md](architecture.md#materials-that-skip-the-check).

`Machine.total_hours` is **never** set by seeding. It only accumulates from
transformations that log usage.

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
| `lint` | `ruff check .`, then `ruff format --check .` |
| `docker-build` | `docker build` — catches a broken image or a failing `collectstatic` |
| `test` | `manage.py check` → `manage.py makemigrations --check --dry-run` → `manage.py test` against a Postgres 16 service |

The middle step of the test job is the one that surprises people: **a model
change without its migration file fails CI even when every test passes.** Run
`makemigrations` and commit the result alongside any `models.py` edit.

**Documentation and dotfile changes skip CI entirely.** Both triggers carry a
`paths-ignore` list covering `**.md` plus dotfiles and dot-directories at any
depth. A commit touching nothing but those runs **no jobs at all**; a commit
touching even one other file runs the full pipeline.

In practice the exempt set is exactly: `*.md` anywhere, `.gitignore`,
`.dockerignore`, `.env.example`, and everything under `.github/`. Note that
`Dockerfile`, `docker-compose.yml`, `pyproject.toml`, and `requirements*.txt`
are **not** dotfiles and still trigger CI.

> **Two of the exempt files do affect CI.** `.dockerignore` determines what
> lands in the image — it is what keeps `.env` and `static/xlsx/` out — and
> `.github/workflows/ci.yml` is the pipeline itself. A change to either ships
> unverified until the next code push. If you edit them, push a trivial code
> change alongside, or trigger a run manually.

> **Watch out if `main` has branch protection** with these jobs as *required
> status checks*. A skipped workflow reports nothing rather than success, so a
> docs-only pull request would sit unmergeable, waiting for a check that will
> never arrive. The standard fix is a second workflow, triggered on the same
> ignored paths, with jobs of the same names that do nothing and pass.

> **Watch out if `main` has branch protection** with these jobs as *required
> status checks*. A skipped workflow reports nothing rather than success, so a
> docs-only pull request would sit unmergeable, waiting for a check that will
> never arrive. The standard fix is a second workflow, triggered on `**.md`
> only, with jobs of the same names that do nothing and pass.

What CI does **not** cover: static-file serving. The test job runs with the
plain storage backend and never requests a `/static/` URL, and `docker build`
does not start the container. Verify static changes by hand — see
[configuration.md](configuration.md#verifying-static-files).

Dependabot (`.github/dependabot.yml`) opens weekly update pull requests for pip
packages, Docker base images, and the Actions themselves.

## Conventions

- **User-facing text is hardcoded Czech**, not `gettext`. Do not introduce
  `{% trans %}` half-way; see [localization.md](localization.md).
- Code, comments, docstrings, and these docs are English.
- Comments explain *why*, not *what*. The existing ones are load-bearing —
  several record a decision that looks wrong until you know the reason.
- Match the surrounding code's density and idiom rather than importing a
  different house style.
