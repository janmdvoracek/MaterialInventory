# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

MaterialInventory is a simple app to keep track of incoming and outgoing material, as well as its processing and transformation.

## Tech stack

Django 6 + Django REST Framework, PostgreSQL, server-rendered templates with HTMX for interactivity (no separate JS frontend). Depot workers fill out mobile-first web forms; Django's admin serves as the back-office UI for managers.

## Architecture

Modular monolith, one Django project (`config`) with these apps:

- `accounts` — custom `User` model with a `role` field (`WORKER`/`MANAGER`/`ADMIN`) and an `is_manager_or_admin` property. `accounts/decorators.py::role_required(*roles)` gates privileged views (401/redirect if anonymous, 403 if wrong role).
- `materials` — `Material` catalog (SKU, unit of measure, category, `track_stock` — off for materials that never get a formal Receipt, e.g. on-site-excavated source material, so stock-sufficiency checks skip them everywhere they'd otherwise be consumed), `Location` (depot zones/bins), and `Machine` (name, `is_active`, `total_hours` — a simple running counter, not derived from usage records — and an optional `hourly_rate` for reference). `materials/management/commands/seed_data.py` bulk-loads materials/locations/machines/users from CSV (see Commands below).
- `inventory` — `StockMovement`, an **append-only ledger**. Current stock is always derived by summing signed quantities per material/location, never stored as a mutable counter. `movement_type` is one of `RECEIPT`, `SHIPMENT`, `TRANSFORM_CONSUME`, `TRANSFORM_PRODUCE`, `ADJUSTMENT` (the last restricted to Manager/Admin). `inventory/services.py::get_available_quantity(material, location, lock=False)` is the shared stock-check helper, used with `lock=True` inside `transaction.atomic()` to close check-then-write races on shipments and transform-consume. `inventory/views.py` also has a filterable, paginated movement history view plus a streaming CSV export.
- `workorders` — `WorkOrder` groups the set of `StockMovement`s from a single transformation job (materials consumed + materials produced). Consumed quantities for the same material+location are combined across formset rows and stock-checked before any writes; the whole submission is atomic. A job can also log one or more `MachineUsage` rows (machine + hours, chained machines supported) in the same atomic block — each usage increments its `Machine.total_hours` via an `F()` update, and a stock shortfall rolls back machine usage too.
- `api` — reserved for DRF endpoints if a non-HTML client is ever needed; not yet built.

Mobile forms (receive / ship / transform / adjust) live under `inventory/` and `workorders/` views+templates, styled mobile-first, with a bottom nav (Adjust hidden from plain Workers) plus a stock dashboard and movement history/export.

User-facing copy (auth forms in `accounts/forms.py`, movement history export headers in `inventory/views.py`, etc.) is written in Czech for depot workers; `LANGUAGE_CODE` itself is left at `en-us` — this is done by hardcoding Czech strings/labels directly rather than through Django's i18n framework, so keep new user-facing text in the same style rather than introducing `{% trans %}`/`gettext` half-way.

## Commands

Local dev (without Docker), from a virtualenv with `requirements.txt` installed:

```bash
python manage.py runserver
```

```bash
python manage.py makemigrations
python manage.py migrate
```

```bash
python manage.py createsuperuser
```

```bash
python manage.py test
```

Run a single app's tests, or a single test case/method, the same way:

```bash
python manage.py test materials
python manage.py test materials.tests.MaterialModelTests.test_material_str
```

Seed the material catalog, depot locations, machinery, and employee accounts from CSV (idempotent — safe to re-run; matches the `.env`/`.env.example` pattern, real files are gitignored):

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
cp seed_data/locations.example.csv seed_data/locations.csv
cp seed_data/machines.example.csv seed_data/machines.csv
cp seed_data/users.example.csv seed_data/users.csv
# edit those four files with real data, then:
python manage.py seed_data
```

`machines.csv` takes `name,hourly_rate` — a machine's `total_hours` is never set by seeding, only accumulated by transformations that log usage against it. `materials.csv` takes an optional `track_stock` column (`true`/`false`).

Optional columns (`track_stock`, `hourly_rate`) are only written when the CSV actually carries a value for that row: omitting the column, or leaving the cell blank, **preserves** whatever is already in the database, so re-seeding never clobbers a value someone set by hand in the admin. New records still fall back to the model defaults (`track_stock=True`, `hourly_rate=NULL`). Clearing a value back to empty is an admin action, not a CSV one.

New users get a random temporary password printed to the console (share it securely); only `ADMIN`-role users get Django admin access — both `is_staff` (to log in) and `is_superuser` (to actually see/edit anything there; `is_staff` alone gets an empty "you don't have permission" admin index). Managers do not get admin access at all — they keep everything driven by `role_required`/`is_manager_or_admin` in the app itself (e.g. stock Adjustments), just not the Django admin backend.

Full stack via Docker Compose (Django + Postgres):

```bash
cp .env.example .env
docker compose up --build
```

Then in another terminal, run migrations inside the container:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Lint/format with Ruff (config in `pyproject.toml`; `requirements-dev.txt` pins the version used in CI):

```bash
pip install -r requirements-dev.txt
ruff check .
ruff format .
```

CI (`.github/workflows/ci.yml`) runs `ruff check` and `ruff format --check` (must pass, not just run), a `docker build` sanity check, and the Django test suite as separate jobs on every push/PR. Dependabot (`.github/dependabot.yml`) opens weekly update PRs for pip, Docker base images, and the Actions themselves.

## Deployment

Only the dev-oriented `docker-compose.yml` exists today (`runserver`, `DEBUG=True`, DB port exposed). A LAN-only production deployment to a company server is planned but not yet implemented — see [DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md) for the approach and the known blockers it identified (`collectstatic` never run against whitenoise's manifest storage, and a `.gitignore` bug that silently excludes the `seed_data/*.example.csv` files).
