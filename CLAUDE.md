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

Seed the material catalog, depot locations, machinery, and employee accounts from CSV (idempotent — safe to re-run; matches the `.env`/`.env.example` pattern, real files are gitignored):

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
cp seed_data/locations.example.csv seed_data/locations.csv
cp seed_data/machines.example.csv seed_data/machines.csv
cp seed_data/users.example.csv seed_data/users.csv
# edit those four files with real data, then:
python manage.py seed_data
```

`machines.csv` takes `name,hourly_rate` — a machine's `total_hours` is never set by seeding, only accumulated by transformations that log usage against it. `materials.csv` takes an optional `track_stock` column (`true`/`false`, defaults to `true` if omitted).

New users get a random temporary password printed to the console (share it securely); only `ADMIN`-role users get Django admin (`is_staff`) access.

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

No linter/formatter is configured yet.
