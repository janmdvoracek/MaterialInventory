# MaterialInventory

[![CI](https://github.com/janmdvoracek/MaterialInventory/actions/workflows/ci.yml/badge.svg)](https://github.com/janmdvoracek/MaterialInventory/actions/workflows/ci.yml)

Job tracking for a materials depot: what each processing job consumed and
produced, who worked it and for how long, and how many machine hours it took.

Depot workers write up each job from their phones on a mobile-first web form.
The app's interface is entirely in **Czech**; this repository's documentation
and code are in English.

> **Note:** despite the name, this app does **not** track stock levels. Receipts,
> shipments, adjustments, the stock dashboard and the movement history were all
> removed; there are no balances and no sufficiency checks. It records what was
> processed, not what is on hand.

---

## What it does

| | |
|---|---|
| **Transform** (*Zpracování*) | The main form, and the landing page. One job consumes some materials and produces others — crushing, sorting, cutting — logs the submitter's own hours, names collaborators with their hours, and records machine motohodiny and the tonnage each machine processed. |
| **Review** (*Přehled*) | Every recorded job with everything that was typed into the form, filterable, with the ones awaiting a decision highlighted. A manager approves, corrects or deletes from here. Manager/Admin only. |
| **Machines** (*Stroje*) | Hours per machine and its two reference rates (Kč/hod, Kč/t), plus a filterable usage log. Manager/Admin nav entry. |
| **Hours** (*Hodiny*) | Hours worked per person, for payroll and job costing. For a worker, also their own last few jobs with the review status of each. |

A job is written as a single transaction: its material line items, every
participant's hours, and any machine usage all land together or not at all.
Labour hours and machine motohodiny are deliberately separate numbers — neither
is derived from the other. See [docs/architecture.md](docs/architecture.md).

**A job a worker submits does not count until a manager approves it.** It is
recorded straight away and shows up highlighted on the Review page, but the
Hours, Machines and machine-history pages report approved jobs only. A manager's
own submission is approved as it is written — there is nobody above them to sign
it off.

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

The catalog, machines, and staff accounts are seeded from CSV:

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
cp seed_data/machines.example.csv seed_data/machines.csv
cp seed_data/users.example.csv seed_data/users.csv
# edit those three with real data, then:
python manage.py seed_data
```

Re-running is safe — it updates in place rather than duplicating, and never
overwrites a value someone set by hand in the admin. New accounts get a random
temporary password printed to the console; share it securely.
Full column reference: [docs/development.md](docs/development.md#seeding-data).

## Common commands

```bash
python manage.py test                  # full suite (182 tests, needs Postgres)
python manage.py test workorders       # one app
ruff check . && ruff format .          # lint and format
docker compose up --build              # full stack
```

More, including single-test invocation and the CI pipeline:
[docs/development.md](docs/development.md).

## Roles

| Role | Can do |
|---|---|
| `WORKER` | Transform and Hours. Sees only their own hours and machine usage, plus jobs they were named a collaborator on. Their submissions wait for approval; a job sent back is shown to them on the Transform page with the reason, but they cannot edit it. |
| `MANAGER` | Everything a Worker can, plus the Review page (approve / edit / delete any job), the Machines pages, and an unrestricted view of everyone's hours and machine usage. Their own submissions are approved on the spot. **No** Django admin access. |
| `ADMIN` | Everything, plus the Django admin back office for editing the catalog and accounts. |

Workers' scoping is enforced in the database query, not by hiding form fields —
editing the URL does not widen what they can see. The Review pages are gated in
the view with `role_required`, so a worker who types the URL gets a 403. The
Machines pages are the exception: they are hidden from a worker's nav but not
gated, so a worker who types that URL still gets in.

## Project layout

```
config/       Django project — settings, root URLconf, WSGI/ASGI
accounts/     Custom User model, roles, role_required decorator, Czech auth forms
materials/    Material / Machine catalog + the seed_data command
inventory/    StockMovement — the material line items of a job. Model + admin only.
workorders/   Transform form, job review dashboard, machine dashboard/history, time-worked reporting, all URLs
templates/    All HTML; base.html holds the site CSS and bottom nav
static/       Source static assets (tracked; NOT the collectstatic output)
seed_data/    CSV templates — real data files are gitignored
locale/       Czech overrides for Django's own untranslated strings (.po + .mo)
docs/         Documentation (see below)
```

## Documentation

| Document | For |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How work orders, line items, hours and roles fit together. |
| [docs/development.md](docs/development.md) | Environment setup, testing, linting, seeding, CI. |
| [docs/configuration.md](docs/configuration.md) | Every environment variable and the settings that need explaining. |
| [docs/localization.md](docs/localization.md) | The Czech locale's consequences for numbers, dates and forms. Non-obvious; read it before touching either. |
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
