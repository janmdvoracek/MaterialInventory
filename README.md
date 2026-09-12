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
| **Machines** (*Stroje*) | One filter over hours and tonnage per machine, its two rates (Kč/hod, Kč/t), what those come to in money — per hours, per tonnes, and in total — and the individual usage rows behind it all. Manager/Admin nav entry. |
| **Materials** (*Materiál*) | Tonnage consumed, produced and net per material over a date range, and the individual line items behind those totals — "how much 8/16 did we make last month?". Manager/Admin only. |
| **Hours** (*Hodiny*) | Hours worked per person, for payroll and job costing. For a worker, also their own last few jobs with the review status of each. |

The summary table on each of those last three downloads as a CSV that opens
straight into Excel — semicolon-delimited, UTF-8 BOM, comma decimals — over
whatever filter the page is showing.

A job is written as a single transaction: its material line items, every
participant's hours, and any machine usage all land together or not at all.
Labour hours and machine motohodiny are deliberately separate numbers — neither
is derived from the other. See [docs/architecture.md](docs/architecture.md).

**A job a worker submits does not count until a manager approves it.** It is
recorded straight away and shows up highlighted on the Review page, but the
Hours and Machines pages report approved jobs only. A manager's
own submission is approved as it is written — there is nobody above them to sign
it off.

## Tech stack

Django 6.1, PostgreSQL 16, server-rendered Django templates. **No JavaScript at
all** — not a build step, not a framework, not a CDN tag: every page is a plain
form POST, so the app depends on no CDN and stays quick on a phone over patchy
mobile data. Even the
*„+ další řádek"* / *„− odebrat řádek"* buttons on the entry form are submits that
come back with a resized form, not script.
WhiteNoise serves static files; Gunicorn runs the app in production. Ruff
handles linting and formatting.

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
python manage.py test                  # full suite (314 tests, needs Postgres)
python manage.py test workorders       # one app
ruff check . && ruff format .          # lint and format
docker compose up --build              # full stack
```

More, including single-test invocation and the CI pipeline:
[docs/development.md](docs/development.md).

## Roles

| Role | Can do |
|---|---|
| `WORKER` | Transform and Hours. Sees only their own hours, plus jobs they were named a collaborator on. Their submissions wait for approval, and their last five are listed with a status badge at the top of Hours; they cannot edit them. |
| `MANAGER` | Everything a Worker can, plus the Review page (approve / edit / delete any job), the Machines page, and an unrestricted view of everyone's hours and machine usage. Their own submissions are approved on the spot. **No** Django admin access. |
| `ADMIN` | Everything, plus the Django admin back office for editing the catalog and accounts. |

Workers' scoping is enforced in the database query, not by hiding form fields —
editing the URL does not widen what they can see. The Review pages and the
Machines page are gated in the view with `role_required`, so a worker who types
one of those URLs gets a 403 rather than a scoped-down page.

## Project layout

```
config/       Django project — settings, root URLconf, WSGI/ASGI
accounts/     Custom User model, roles, role_required decorator, Czech auth forms
materials/    Material catalog + the seed_data command (materials, machines, users)
machines/     Machine catalog with its hourly and per-tonne rates
workorders/   Jobs and their StockMovement line items; Transform form, review dashboard, machine and material pages, time-worked reporting, all URLs (views.py: jobs; reports.py: reports)
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
| [DEPLOYMENT.md](docs/deployment/DEPLOYMENT.md) | Runbook for the public VPS deployment — the steps you run on the server. |
| [DEPLOYMENT_PLAN_PUBLIC.md](docs/deployment/DEPLOYMENT_PLAN_PUBLIC.md) | Why that deployment is shaped the way it is: TLS, the proxy, the HTTPS settings, the security gaps the LAN used to cover. |
| [DEPLOYMENT_PLAN.md](docs/deployment/DEPLOYMENT_PLAN.md) | The superseded LAN plan. Still the authority on the image, static files, seeding, collation and backups, none of which changed. |
| [CLAUDE.md](CLAUDE.md) | Working notes for AI coding assistants. Overlaps the docs above but is written as instructions, not explanation. |

## Deployment

Not yet deployed, but everything needed to deploy is in the repo. The target is
a **public VPS behind the company domain, over HTTPS**.

`docker-compose.yml` is **development only** — `runserver`, `DEBUG=True`, the
database port published to the host — and must never be brought up on the
server, where a published port means a published-to-the-internet port. The
production stack is `docker-compose.prod.yml`, three services: `db` and `web`
(gunicorn from the built image, hashed assets served by WhiteNoise) both
unpublished, and `proxy` (Caddy) owning 80/443 and obtaining its own Let's
Encrypt certificate.

```bash
cp .env.production.example .env.production   # then fill it in
```

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Follow [DEPLOYMENT.md](docs/deployment/DEPLOYMENT.md) on the server rather than those two lines.
Ordering matters in several places — the DNS record has to exist before the
first `up` or there is no certificate, and the database collation has to be
checked before any data exists. That runbook also opens with two security
gaps the old LAN deployment closed with the network rather than with code; read
them before pointing DNS at anything.
[DEPLOYMENT_PLAN_PUBLIC.md](docs/deployment/DEPLOYMENT_PLAN_PUBLIC.md) records why the setup
looks the way it does.

## License

No `LICENSE` file is present; treat this as private and internal. Add one before
distributing the code.
