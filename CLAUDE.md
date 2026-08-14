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

User-facing copy (auth forms in `accounts/forms.py`, movement history export headers in `inventory/views.py`, model `TextChoices` labels, etc.) is written in Czech for depot workers by **hardcoding Czech strings/labels directly** rather than going through Django's i18n framework — keep new user-facing text in the same style rather than introducing `{% trans %}`/`gettext` half-way.

`LANGUAGE_CODE = 'cs'` and `TIME_ZONE = 'Europe/Prague'` cover the parts the app doesn't write itself. The language setting is what makes **Django's own** strings Czech — admin chrome, `contrib.auth`, and form validation errors (`Toto pole je vyžadováno.`, `Zadejte číslo.`) — using the `.mo` catalogs Django ships. `LocaleMiddleware` is deliberately *not* installed, so the language is fixed rather than negotiated per-request from `Accept-Language`. The two mechanisms are complementary: the setting handles framework strings, hardcoding handles app copy.

Consequences of the locale worth knowing before you touch numbers or dates:

- **Template output of numbers is localised** — `{{ movement.quantity }}` renders `12,50`, not `12.50`. Tests that assert on rendered quantities must use the comma. Form inputs are *not* localised (fields default to `localize=False`), so `NumberInput` still renders and accepts `value="12.50"` with a dot, and a comma typed into a `DecimalField` is rejected with `Zadejte číslo.` Leave it that way — setting `localize=True` would downgrade the widget from `<input type="number">` to a plain text input and cost mobile users their numeric keypad.
- **Date input still round-trips.** Czech `DATE_INPUT_FORMATS` is `%d.%m.%Y`-first and does not list ISO, but Django appends `%Y-%m-%d` to every locale's list, so the `<input type="date">` widgets on the history/time-worked filters parse normally. Bound forms re-render the raw submitted string, so filters survive pagination. Beware only *unbound* date fields with a python-`date` `initial`: those render as `14.08.2026`, which an `<input type="date">` rejects as invalid and shows blank. No form does this today.
- **Every `ModelChoiceField` needs an explicit `empty_label`.** Django 6 defaults it to `- Select an option -`, which is *not* a translatable string — it renders English even under `cs`, so it cannot be fixed by the locale. Entry forms (receive/ship/adjust/transform) use a prompt: `Vyberte materiál` / `Vyberte lokalitu` / `Vyberte stroj`. Filter forms use an "all" phrasing, since a blank there means "don't filter": `Všechny materiály`, `Všichni uživatelé`, and so on — matching the `Všechny typy` choice `HistoryFilterForm.__init__` sets for `movement_type`. `EmptyLabelTests` in `inventory/tests.py` and `workorders/tests.py` fail if a new field forgets. `ModelMultipleChoiceField` (e.g. `collaborators`) has no blank option and needs nothing.
- **Keep explicit date formats in templates.** Timestamps use `|date:"Y-m-d H:i"`, which is locale-independent; a bare `{{ ... }}` on a date would render as `14. srpna 2026` instead.
- **The movement-history CSV export targets Czech Excel, not RFC 4180.** It is semicolon-separated (the Windows list separator), writes comma decimals (`-12,500`), formats dates `dd.mm.yyyy hh:mm:ss` so Excel parses them as dates instead of text, and opens with a UTF-8 BOM — without the BOM Excel assumes windows-1250 and mangles every diacritic in the material names. `ExportExcelCompatibilityTests` asserts all four against the raw bytes. Quantities go through `_csv_number`, deliberately *not* `_csv_safe`: the formula-injection guard prefixes an apostrophe to anything starting with `-`, which would stop Excel treating negative shipment quantities as numbers. Anything machine-reading this file needs `delimiter=';'` and `decode('utf-8-sig')`.
- **`USE_TZ` stays on, so the DB still stores UTC** — `TIME_ZONE` only affects rendering and the day boundaries `__date` lookups use for the history date filters. Python code does *not* auto-localise, so anything formatting a datetime outside a template needs `timezone.localtime()` explicitly (the CSV export does this).

Not yet localised: model field `verbose_name`s are still auto-derived English, so the admin shows Czech chrome over English field labels (`Movement type`, `Created at`). Acceptable because the admin is back-office for `ADMIN`-role users only.

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

Tests are one `tests.py` per app, plain `django.test.TestCase`, no pytest and no mocking — almost everything drives a real view through `self.client` and asserts on both `response.context` and the resulting rows. Postgres is required; there is no SQLite fallback, and no separate test settings module, so a change to `config/settings.py` (locale included) is a change to how the suite behaves.

Two exceptions to that shape, both in `inventory/tests.py`:

- `StockLockConcurrencyTests` is a **`TransactionTestCase`**, not a `TestCase`, because it runs real concurrent transactions in threads to exercise the `select_for_update()` race that `get_available_quantity(lock=True)` exists to close — a `TestCase` wraps the whole test in one transaction and would hide the interleaving. Worker threads must `connection.close()` in a `finally`, and exceptions are collected into a list and re-asserted in the main thread rather than raised (a thread that dies silently would otherwise turn into a passing test — hence the `# noqa: BLE001`). If you change the locking, sanity-check these by temporarily neutering the `select_for_update()` and confirming both tests fail.
- `DashboardTemplateTests` / `MovementHistoryTemplateTests` assert on rendered HTML rather than context, covering the things context assertions structurally cannot: the Czech comma decimal separator, local-time timestamps, and the `qty-negative` styling on negative stock. Match `class="qty-negative"`, not the bare class name — `base.html` ships a `td.qty-negative` CSS rule on every page, so the bare string is always present.

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
