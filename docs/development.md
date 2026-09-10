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
python manage.py test workorders                                   # one app
python manage.py test materials.tests.MaterialModelTests           # one class
python manage.py test materials.tests.MaterialModelTests.test_material_str
```

280 tests, roughly three and a half minutes. Postgres must be reachable.

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

**There is no `inventory/tests.py`.** The app holds nothing but migrations, and
`StockMovement`'s model-level tests moved into `workorders/tests.py` along with
the model. That file is where the app is actually covered.

**`EmptyLabelTests`** (`workorders/tests.py`) fails if any `ModelChoiceField`
forgets its `empty_label`. See
[localization.md](localization.md#every-modelchoicefield-needs-an-empty_label).

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
target, migrations excluded. Three rules are disabled with rationale in the
file — Django's `class Meta` mutables, quoted `Decimal()` literals, and a
naive-datetime rule that only fires in test helpers.

**CI runs `ruff format --check`**, which fails on unformatted code rather than
fixing it. Run `ruff format .` before pushing.

## Seeding data

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
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
| `materials.csv` | `sku`, `name` |
| `machines.csv` | `name`, `hourly_rate` *(optional)*, `rate_per_ton` *(optional)* |
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
| `lint` | `ruff check .`, then `ruff format --check .` |
| `docker-build` | `docker build` — catches a broken image or a failing `collectstatic` |
| `test` | `manage.py check` → `manage.py makemigrations --check --dry-run` → `manage.py test` against a Postgres 16 service |

The middle step of the test job is the one that surprises people: **a model
change without its migration file fails CI even when every test passes.** Run
`makemigrations` and commit the result alongside any `models.py` edit.

**Documentation-only changes skip CI entirely.** Both triggers carry a
`paths-ignore` list, and since `30c6477` it holds a single pattern: `'**.md'`.
A commit touching nothing but Markdown runs **no jobs at all**; a commit
touching even one other file runs the full pipeline.

Dotfiles and dot-directories used to be exempt too, via four extra globs
(`.*`, `.*/**`, `**/.*`, `**/.*/**`). Those were removed, so `.gitignore`,
`.dockerignore`, `.env.example`, and everything under `.github/` now trigger
CI like any other file — which is the safer default, since `.dockerignore`
decides what lands in the image and `ci.yml` *is* the pipeline.

> **The comment block above `on:` in `ci.yml` was not updated with the keys.**
> It still explains the four dotfile globs as though they were live. Read the
> `paths-ignore` keys themselves; the prose above them is stale.

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
