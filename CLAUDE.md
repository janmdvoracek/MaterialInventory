# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

MaterialInventory is a simple app to keep track of incoming and outgoing material, as well as its processing and transformation.

## Tech stack

Django 6 + Django REST Framework, PostgreSQL, server-rendered templates with HTMX for interactivity (no separate JS frontend). Depot workers fill out mobile-first web forms; Django's admin serves as the back-office UI for managers.

## Architecture

Modular monolith, one Django project (`config`) with these apps:

- `accounts` — custom `User` model with a `role` field (`WORKER`/`MANAGER`/`ADMIN`)
- `materials` — `Material` catalog (SKU, unit of measure, category) and `Location` (depot zones/bins)
- `inventory` — `StockMovement`, an **append-only ledger**. Current stock is always derived by summing signed quantities per material/location, never stored as a mutable counter. `movement_type` is one of `RECEIPT`, `SHIPMENT`, `TRANSFORM_CONSUME`, `TRANSFORM_PRODUCE`, `ADJUSTMENT`.
- `workorders` — `WorkOrder` groups the set of `StockMovement`s from a single transformation job (materials consumed + materials produced), created atomically in one form submission.
- `api` — reserved for DRF endpoints if a non-HTML client is ever needed; not yet built.

Mobile forms (receive / ship / transform) live under `inventory/` and `workorders/` views+templates, styled mobile-first, with a bottom nav for the three actions plus a stock dashboard.

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
