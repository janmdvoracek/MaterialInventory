# MaterialInventory

[![CI](https://github.com/janmdvoracek/MaterialInventory/actions/workflows/ci.yml/badge.svg)](https://github.com/janmdvoracek/MaterialInventory/actions/workflows/ci.yml)

Stock tracking for a materials depot: what came in, what went out, what was
transformed into what, and how many machine hours it took.

Depot workers record every movement from their phones on a mobile-first web
form. Managers get filtered history, CSV exports for Excel, and manual stock
corrections. The app's interface is entirely in **Czech**; this repository's
documentation and code are in English.

---

## What it does

| | |
|---|---|
| **Receipt** (*Příjem*) | Material arrives at a location. |
| **Shipment** (*Výdej*) | Material leaves. Blocked if stock is insufficient. |
| **Transform** (*Zpracování*) | One job consumes some materials and produces others — crushing, sorting, cutting. Optionally logs machine hours and names collaborators. |
| **Adjustment** (*Ruční úprava*) | Manual correction after a stocktake. Manager/Admin only, and always requires a written reason. |
| **Stock dashboard** (*Sklad*) | Current quantity per material per location. |
| **History** (*Historie*) | Every movement, filterable, paginated, exportable to Excel-friendly CSV. |
| **Machines** (*Stroje*) | Running total of hours per machine, plus a usage log. |
| **Hours** (*Hodiny*) | Hours worked per person, for payroll and job costing. |

The central design decision: **stock is never stored as a number.** Every event
appends a signed row to an immutable ledger, and the current quantity is always
`SUM(quantity)` for that material and location. Nothing is ever edited or
deleted in place, so the stock figure and its full audit trail can never
disagree. See [docs/architecture.md](docs/architecture.md).

## Tech stack

Django 6.1, PostgreSQL 16, server-rendered Django templates with no JavaScript
build step. WhiteNoise serves static files; Gunicorn runs the app in production.
Ruff handles linting and formatting.

Python is 3.12 locally and in CI, but the Docker image builds on 3.14 — see
[configuration.md](docs/configuration.md#python-version).

## Quick start

### With Docker (recommended)

```bash
cp .env.example .env
docker compose up --build
```

Then, in a second terminal:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

The app is at <http://localhost:8000>.

### Without Docker

Requires a running PostgreSQL instance.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then set DB_HOST=localhost
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

> **Note:** `createsuperuser` does not set a `role`, so the account defaults to
> `WORKER`. It still gets full access — superusers bypass every role check — but
> if you later edit it in the Django admin, set its role to **Admin** or you
> will strip its own access. See [docs/architecture.md](docs/architecture.md#roles-and-permissions).

### Load real data

The catalog, locations, machines, and staff accounts are seeded from CSV:

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
cp seed_data/locations.example.csv seed_data/locations.csv
cp seed_data/machines.example.csv seed_data/machines.csv
cp seed_data/users.example.csv seed_data/users.csv
# edit those four with real data, then:
python manage.py seed_data
```

Re-running is safe — it updates in place rather than duplicating, and never
overwrites a value someone set by hand in the admin. New accounts get a random
temporary password printed to the console; share it securely.
Full column reference: [docs/development.md](docs/development.md#seeding-data).

## Common commands

```bash
python manage.py test                  # full suite (171 tests, needs Postgres)
python manage.py test inventory        # one app
ruff check . && ruff format .          # lint and format
docker compose up --build              # full stack
```

More, including single-test invocation and the CI pipeline:
[docs/development.md](docs/development.md).

## Roles

| Role | Can do |
|---|---|
| `WORKER` | Receipt, Shipment, Transform. Sees only their own movements and hours, plus jobs they were named a collaborator on. |
| `MANAGER` | Everything a Worker can, plus Adjustments and an unrestricted view of everyone's history, hours, and exports. **No** Django admin access. |
| `ADMIN` | Everything, plus the Django admin back office for editing the catalog and accounts. |

Workers' scoping is enforced in the database query, not by hiding form fields —
editing the URL does not widen what they can see.

## Project layout

```
config/       Django project — settings, root URLconf, WSGI/ASGI
accounts/     Custom User model, roles, role_required decorator, Czech auth forms
materials/    Material / Location / Machine catalog + the seed_data command
inventory/    StockMovement ledger, stock dashboard, history, CSV export
workorders/   Transform jobs, machine usage, time-worked reporting
templates/    All HTML; base.html holds the site CSS and bottom nav
static/       Source static assets (tracked; NOT the collectstatic output)
seed_data/    CSV templates — real data files are gitignored
locale/       Czech overrides for Django's own untranslated strings (.po + .mo)
docs/         Documentation (see below)
```

## Documentation

| Document | For |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How the ledger, work orders, roles, and concurrency control fit together. Read before changing anything that writes stock. |
| [docs/development.md](docs/development.md) | Environment setup, testing, linting, seeding, CI. |
| [docs/configuration.md](docs/configuration.md) | Every environment variable and the settings that need explaining. |
| [docs/localization.md](docs/localization.md) | The Czech locale's consequences for numbers, dates, forms, and the CSV export. Non-obvious; read it before touching either. |
| [docs/user-guide.cs.md](docs/user-guide.cs.md) | End-user manual, in Czech, for depot staff. |
| [DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md) | The plan for the LAN-only company-server deployment. Partly implemented. |
| [CLAUDE.md](CLAUDE.md) | Working notes for AI coding assistants. Overlaps the docs above but is written as instructions, not explanation. |

## Deployment

Not yet deployed. `docker-compose.yml` is **development only** — it runs
`runserver` with `DEBUG=True` and exposes the database port to the host.

The groundwork is in place: `collectstatic` runs as a Docker build step and
serves hashed, compressed assets through WhiteNoise. Still outstanding are the
production Compose file, `.env.production.example`, a database backup script,
and the server runbook. See [DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md).

## License

No `LICENSE` file is present; treat this as private and internal. Add one before
distributing the code.
