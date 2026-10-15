# MaterialInventory

[![CI](https://github.com/janmdvoracek/MaterialInventory/actions/workflows/ci.yml/badge.svg)](https://github.com/janmdvoracek/MaterialInventory/actions/workflows/ci.yml)

Job tracking for a materials depot: what each processing job consumed and produced, who worked on it and for how long, which machines ran and what fuel went into them. Workers record jobs from their phones. The interface is **Czech**; the code and docs are English.

> Despite the name, this app does **not** track stock levels. It records what was processed, not what is on hand.

## What it does

| Page | |
|---|---|
| **Zpracování** (record a job) | The landing page. The worker enters location, materials consumed and produced, their own and collaborators' hours, the machines used (motohodiny, tonnage, fuel) and up to ten photos. The consumed and produced totals must match. A job that is only refuelling is valid on its own. |
| **Přehled** (review) | Every job, with pending ones highlighted. A manager approves, corrects or deletes. Manager only. |
| **Stroje** (machines) | Per machine: meter reading, hours run, tonnage, rates, cost, and fuel, plus the rows behind each figure. Manager only. |
| **Materiál** (materials) | Tonnes consumed, produced and net per material, plus the line items. Manager only. |
| **Hodiny** (hours) | Hours per person. A worker also sees their last five submissions and their review status. |

A few more things:

- **Approval.** A worker's job counts in reports only once a manager approves it; a manager's own job is approved immediately.
- **Exports.** Every summary table, and the material and fuel detail tables, download as an Excel-friendly CSV of whatever the filter shows.
- **Photos.** They are shrunk on upload and are never publicly reachable.
- **Phone app.** The site installs to a phone's home screen as **VyrobaPK**.

## Tech stack

Django 6, PostgreSQL 16, server-rendered templates, gunicorn and WhiteNoise in production, Caddy in front. There is no build step, no JS framework and no CDN. Three small scripts on the job form are pure enhancement: without them every page is still a plain form POST. Python 3.14.

## Quick start

With Docker:

```bash
cp .env.example .env
docker compose up --build
docker compose exec web python manage.py migrate          # in a second terminal
docker compose exec web python manage.py createsuperuser
```

Then open <http://localhost:8000>. Without Docker, see [docs/development.md](docs/development.md).

`createsuperuser` leaves the role at `WORKER`. Superusers bypass role checks anyway, but if you edit the account in the admin, set its role to **Admin**.

To load the catalog, machines, locations and staff accounts, copy the four `seed_data/*.example.csv` files to `*.csv`, edit them and run `python manage.py seed_data`. It is safe to re-run. See [docs/development.md](docs/development.md#seeding-data).

## Common commands

```bash
python manage.py test --parallel               # needs Postgres
coverage run manage.py test --parallel && coverage combine && coverage html   # report in htmlcov/
ruff check . && ruff format .
```

## Roles

| Role | Can do |
|---|---|
| `WORKER` | Record jobs; see their own hours. Submissions wait for approval. |
| `MANAGER` | Everything a worker can, plus Přehled, Stroje, Materiál and everyone's hours. No Django admin. |
| `ADMIN` | Everything, plus the Django admin (catalog and accounts). |

Scoping is enforced in the queries and views, not by hiding links. **`/admin/` answers 404 unless you are already logged in as an Admin**: log in to the app first, then use the header link.

## Project layout

```
config/       settings and root URLconf
accounts/     User model, roles, role_required, the /admin/ gate
materials/    Material catalog and the seed_data command
machines/     Machine catalog and rates
locations/    Location catalog
workorders/   jobs, every page and URL (views.py: jobs; reports.py: reports; pwa.py)
templates/    all HTML
static/       source assets (tracked)
seed_data/    CSV templates; real files are gitignored
locale/       Czech strings Django doesn't ship
docs/         documentation
```

## Documentation

| Document | For |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How jobs, rows, approval, reports and roles fit together |
| [docs/development.md](docs/development.md) | Setup, tests, linting, seeding, CI |
| [docs/configuration.md](docs/configuration.md) | Environment variables and the settings that need explaining |
| [docs/localization.md](docs/localization.md) | What the Czech locale does to numbers, dates and forms |
| [docs/user-guide.cs.md](docs/user-guide.cs.md) | End-user manual, in Czech |
| [docs/deployment/DEPLOYMENT.md](docs/deployment/DEPLOYMENT.md) | Server runbook |
| [CLAUDE.md](CLAUDE.md) | Rules for AI coding assistants |

## Deployment

The target is a public VPS behind the company domain, over HTTPS. Production is `docker-compose.prod.yml`, with three services:

- `db`
- `web` (gunicorn)
- `proxy` (Caddy, which gets its own Let's Encrypt certificate)

`docker-compose.yml` is for development only and must never run on the server, because it publishes Postgres. Follow [DEPLOYMENT.md](docs/deployment/DEPLOYMENT.md); ordering matters (DNS before the first start, collation before any data).

## License

No `LICENSE` file; treat as private and internal.
