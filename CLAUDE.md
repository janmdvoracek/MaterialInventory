# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

MaterialInventory records material **processing jobs** — what a transformation consumed and produced, who worked it and for how long, and which machines ran.

**It no longer tracks stock.** Příjem (receipt), Výdej (shipment) and Úprava (adjustment) were removed along with the stock dashboard, movement history and CSV export, `track_stock`, and every balance/sufficiency check. Nothing sums quantities into an on-hand figure and nothing blocks a job for insufficient material. The one quantity rule left is **within** a single job: a transformation's consumed and produced totals must match (see Architecture), which is mass balance across the form being submitted, not a stock level. The name is now historical — don't reintroduce inventory semantics because the repo is called MaterialInventory.

## Documentation

There is a full prose documentation set under `docs/`, and this file overlaps it heavily — the same facts written as instructions rather than explanation. **A change that invalidates something here almost certainly invalidates a paragraph there too; update both in the same commit.**

| File | Covers | Overlaps this file's |
|---|---|---|
| [docs/architecture.md](docs/architecture.md) | Job line items, work orders, roles, filter forms, time-worked | Architecture |
| [docs/localization.md](docs/localization.md) | Czech locale consequences — numbers, dates, `empty_label` | Locale bullets |
| [docs/development.md](docs/development.md) | Setup, testing, linting, seeding, CI, code conventions | Commands |
| [docs/configuration.md](docs/configuration.md) | Every env var, static-files backend, `.dockerignore`, Python version | Deployment |
| [docs/user-guide.cs.md](docs/user-guide.cs.md) | End-user manual, **in Czech**, for depot staff | — |
| [README.md](README.md) | Entry point: feature table, roles, layout, quick start | — |

`docs/` and `README.md` are English; only `user-guide.cs.md` and the app's own copy are Czech. Both `README.md` and `docs/development.md` quote a hardcoded test count, which drifts every time a test is added.

## Tech stack

Django 6, PostgreSQL, server-rendered templates, no separate JS frontend. Depot workers fill out mobile-first web forms; Django's admin serves as the back-office UI for managers.

Three dependencies are installed but **not actually used yet**, so don't assume their patterns are established: `django_htmx` (the htmx script tag is in `base.html`, but no template has an `hx-*` attribute — every page is a plain form POST + redirect), `widget_tweaks` (no template loads it), and `rest_framework` (configured in `REST_FRAMEWORK` for session auth + `IsAuthenticated`, but there are no serializers, viewsets, or API routes). All page CSS is a single inline `<style>` block in `templates/base.html`; there are no CSS files.

## Architecture

Modular monolith, one Django project (`config`) with these apps:

- `accounts` — custom `User` model with a `role` field (`WORKER`/`MANAGER`/`ADMIN`) and an `is_manager_or_admin` property. `accounts/decorators.py::role_required(*roles)` gates privileged views (401/redirect if anonymous, 403 if wrong role).
- `materials` — `Material` catalog (SKU, unit of measure, category), `Location` (depot zones/bins), and `Machine` (name, `is_active`, `total_hours` — a simple running counter, not derived from usage records — and an optional `hourly_rate` for reference). `materials/management/commands/seed_data.py` bulk-loads materials/locations/machines/users from CSV (see Commands below).
- `inventory` — **model and admin only; it has no views, no forms and no URLs.** `StockMovement` (name kept to avoid a table rename) is one material line item on a job: what it consumed, as a negative quantity, or produced, as a positive one. `movement_type` is `TRANSFORM_CONSUME` or `TRANSFORM_PRODUCE` — nothing else. Rows written before the removal may still carry raw `RECEIPT`/`SHIPMENT`/`ADJUSTMENT` values that are no longer in the enum and render without a Czech label; they were left in the database deliberately. `created_at` stays `default=timezone.now` rather than `auto_now_add` so a job could be back-dated later without a schema change, but nothing overrides it today, so it always equals `recorded_at`.
- `workorders` — **the whole app, effectively.** `WorkOrder` groups the `StockMovement` line items from a single transformation job, plus a `collaborators` M2M. One submission also writes one `WorkerHours` row per participant and any number of `MachineUsage` rows (chained machines supported), all in a single `transaction.atomic()` — the three record types are one job and must not land half-written. Formset rows are written exactly as typed; the view no longer combines repeated material rows, because the only reason it did was to stock-check them as a unit. **A job must balance: it needs at least one consumed row and one produced row, and the two sums must be exactly equal** — `transform_create` compares `sum(consumed)` against `sum(produced)` before opening the transaction and re-renders with a Czech `messages.error` if they differ. Totals, not row-by-row: one input is normally crushed into several output fractions. Exact `Decimal` equality, no tolerance, so `5.00` and `5` pass but `5.01` and `5` do not. The form's `quantity` accepts 2 decimal places while `StockMovement.quantity` stores 3 — a form tighter than its model is fine and needs no migration, but it means the tightest mismatch the check can be tested against is `0.01`, not `0.001`. The check has no database state behind it, which is why it sits in the view ahead of the write rather than in a model `clean()`. The totals in that error message go through `django.utils.formats.localize` — an f-string would print `9.5` where the rest of the UI shows `9,5`. Beyond the Transform form there are three read-only views: a machine dashboard, machine-usage history, and a per-person time-worked report.

The app is four pages: **Zpracování** (`transform_create`, mounted at `/` and the landing page), **Hodiny** (`time_worked`), **Stroje** (`machine_dashboard`) and machine-usage history. `workorders/urls.py` is mounted at the root in `config/urls.py`; `inventory` contributes no URLs at all.

**`WorkerHours` (labour) and `MachineUsage` (runtime) are two unrelated numbers** — don't derive one from the other. `WorkerHours` is what a person typed for themselves on the Transform form: the submitter's own hours come from `WorkOrderForm.hours` ("Moje hodiny", required), and each collaborator's from a `WorkerHoursFormSet` row. `MachineUsage.hours` is motohodiny for a machine. The Hodiny tab reports only `WorkerHours`, so its total has no fixed relationship to `Machine.total_hours`.

`collaborators` is **derived**, not entered: `transform_create` calls `work_order.collaborators.set(worker_hours.keys())` so the M2M can never disagree with the hours rows about who worked the job. Naming the same person on two rows sums their hours rather than tripping the `unique_worker_hours_per_work_order` constraint. `collaborator_queryset` in `workorders/forms.py` excludes the submitter (you can't collaborate with yourself) and, for plain workers, anyone who isn't also a `WORKER`. The one place the two *can* drift is the admin: `WorkOrderAdmin` exposes the `collaborators` multi-select and the `WorkerHoursInline` as independent widgets, so an admin edit can leave a collaborator with no hours row, or vice versa.

`Machine.total_hours` is kept in sync by `MachineUsage.save()`/`delete()`, **not** by the view — an `F()` update that handles create, hours-delta on edit, and reassignment to a different machine. That placement is deliberate: admin inline edits go through the same path, so the counter stays correct there too. Never bulk-write `MachineUsage` with `bulk_create`/`queryset.update()`/`queryset.delete()`, which bypass the model methods and silently desync the counter.

`_time_worked_summary` in `workorders/views.py` sums `WorkerHours.hours` per user over the scoped work orders. The view re-queries by `pk__in` into `scoped` before aggregating, because the collaborator filters join the M2M and would otherwise multiply the `Sum`. Two separate scoping steps, both needed: the *queryset* is narrowed by `_participation_filter` (created-by or collaborator) so a worker only sees jobs they took part in, and then the summary **rows** are filtered again to `row['user'] == request.user` — without that second pass a collaborated job would put the creator's hours on a worker's screen.

All pages are mobile-first templates under `templates/workorders/`. Login and password-change are Django's own generic views, wired in `config/urls.py` with the Czech `CzechAuthenticationForm`/`CzechPasswordChangeForm` from `accounts/forms.py` and templates under `templates/registration/` — `accounts/views.py` is empty and there is no `accounts/urls.py`.

The bottom nav is hardcoded in `templates/base.html` and needs a new page's entry added by hand. Workers see Zpracování / Hodiny; **Stroje is inside `{% if user.is_manager_or_admin %}`**. Be aware that **the nav is ahead of the views**: `machine_dashboard` is plain `@login_required`, so a worker who types the URL still gets in. Nothing in the app carries `@role_required` any more — `adjustment_create` was its only user — though `accounts/decorators.py` is kept and still tested. Hiding a link is not access control; if a page is meant to be manager-only, gate the view too.

Page-scoped CSS is keyed off the URL name: `base.html` renders `<body class="page-{{ request.resolver_match.url_name }}">`. So **renaming a URL name silently drops that page's styling** — grep the `<style>` block for `.page-` before touching `urlpatterns`. There is no test covering the pairing. (No `.page-` rule survives the stock removal, so the block is currently empty of them.)

Two cross-cutting patterns that every list view follows; match them in new views rather than inventing a variant:

- **Worker scoping is enforced in the view queryset, not by hiding fields.** `machine_usage_history` and `time_worked` each filter to `Q(created_by=user) | Q(collaborators=user)` for non-manager/admin users, *and separately* `del` the `created_by`/`worker` field in the filter form's `__init__`. The `del` is only cosmetic — the queryset filter is what makes `?created_by=<someone else>` a no-op, and each view has a `test_worker_cannot_bypass_restriction_via_*_param` test asserting it.
- **An invalid filter form returns `.none()`, never the unfiltered queryset.** Silently ignoring a bad filter would hand back everything under a "these are your filtered results" heading. An *unbound* form (no querystring at all) does show everything — the distinction is `form.is_bound`, checked before `form.is_valid()`.

Superusers bypass role checks in two places — `role_required` and `User.is_manager_or_admin` — because `createsuperuser` never sets a `role`, so a bootstrap admin would otherwise default to `WORKER` and be locked out of the app it administers.

User-facing copy (auth forms in `accounts/forms.py`, form labels/`empty_label`s in `workorders/forms.py`, the `RETIRE_HELP_TEXT` block in `materials/models.py`, model `TextChoices` labels, etc.) is written in Czech for depot workers by **hardcoding Czech strings/labels directly** rather than going through Django's i18n framework — keep new user-facing text in the same style rather than introducing `{% trans %}`/`gettext` half-way.

`LANGUAGE_CODE = 'cs'` and `TIME_ZONE = 'Europe/Prague'` cover the parts the app doesn't write itself. The language setting is what makes **Django's own** strings Czech — admin chrome, `contrib.auth`, and form validation errors (`Toto pole je vyžadováno.`, `Zadejte číslo.`) — using the `.mo` catalogs Django ships. `LocaleMiddleware` is deliberately *not* installed, so the language is fixed rather than negotiated per-request from `Accept-Language`. The two mechanisms are complementary: the setting handles framework strings, hardcoding handles app copy.

Consequences of the locale worth knowing before you touch numbers or dates:

- **Template output of numbers is localised** — `{{ row.hours }}` renders `12,50`, not `12.50`. Tests that assert on rendered quantities must use the comma. `|floatformat:N` localises too, so `{{ machine.total_hours|floatformat:1 }}` renders `6,0 h`. Form inputs are *not* localised (fields default to `localize=False`), so `NumberInput` still renders and accepts `value="12.50"` with a dot, and a comma typed into a `DecimalField` is rejected with `Zadejte číslo.` Leave it that way — setting `localize=True` would downgrade the widget from `<input type="number">` to a plain text input and cost mobile users their numeric keypad.
- **Date input still round-trips.** Czech `DATE_INPUT_FORMATS` is `%d.%m.%Y`-first and does not list ISO, but Django appends `%Y-%m-%d` to every locale's list, so the `<input type="date">` widgets on the history/time-worked filters parse normally. Bound forms re-render the raw submitted string, so filters survive pagination. Beware only *unbound* date fields with a python-`date` `initial`: those render as `14.08.2026`, which an `<input type="date">` rejects as invalid and shows blank. No form does this today.
- **Every `ModelChoiceField` needs an explicit `empty_label`.** Django 6 defaults it to `- Select an option -`, which is *not* a translatable string — it renders English even under `cs`, so it cannot be fixed by the locale. The transform formset rows in `workorders/forms.py` use the bare noun instead — `Materiál` / `Lokalita` / `Stroj` / `Pracovník` — because each row renders the selects side by side with no room for a visible label, so the empty option *is* the label (and for the same reason `hours` on those rows carries a placeholder instead). Filter forms use an "all" phrasing, since a blank there means "don't filter": `Všechny stroje`, `Všichni pracovníci`, `Všichni uživatelé`. `EmptyLabelTests` in `workorders/tests.py` fails if a new field forgets. `ModelMultipleChoiceField` (e.g. `collaborators`) has no blank option and needs nothing.
- **Time is 24-hour everywhere the app renders it** — templates use `H:i` and Czech `TIME_FORMAT` is `G:i`. The app no longer renders a `<input type="datetime-local">` anywhere; that widget was the one place the browser, not the app, picked the format (an English-configured phone showed AM/PM regardless of `lang="cs"`), and it went with the Příjem/Výdej back-dating checkbox. If you reintroduce one, that caveat comes back with it.
- **Keep explicit date formats in templates.** Timestamps use `|date:"Y-m-d H:i"`, which is locale-independent; a bare `{{ ... }}` on a date would render as `14. srpna 2026` instead.
- **`USE_TZ` stays on, so the DB still stores UTC** — `TIME_ZONE` only affects rendering and the day boundaries `__date` lookups use for the history date filters. Python code does *not* auto-localise, so anything formatting a datetime outside a template needs `timezone.localtime()` explicitly. Nothing does today; the CSV export that did was removed.

**The Django admin is fully Czech too, via three separate mechanisms** — a new model or admin needs all three or it will show English in one place:

1. **App labels** — `verbose_name` on each `AppConfig` (`Katalog`, `Zpracování`, `Zakázky`, `Uživatelé`), which is what the admin index groups by.
2. **Model and field names** — Czech `verbose_name`/`verbose_name_plural` in every `Meta`, and an explicit lowercase `verbose_name` on every field (Django capitalises it where needed). Adding one is a migration: `makemigrations` emits `AlterField` for a pure label change, and CI fails without it.
3. **`LOCALE_PATHS` → `locale/cs/LC_MESSAGES/django.po`** for the handful of *Django's own* strings whose `cs` translation Django doesn't ship — `- Select an option -`, the delete-confirmation sentence, the date-hierarchy label, the two screen-reader-only `Search %(name)s` / `Pagination %(name)s` headings. These are not fixable by `LANGUAGE_CODE`, because the msgid simply isn't in the catalogs Django installs. This is the one place the project *does* use the i18n framework, and it holds Django msgids only — app copy stays hardcoded.

The catalog is read from the **compiled `.mo`, and both files are committed**; editing the `.po` alone changes nothing at runtime. Recompile with `python manage.py compilemessages -l cs --ignore=.venv` — without the `--ignore` it walks into the virtualenv and recompiles every catalog Django ships. `AdminCzechTests` in `accounts/tests.py` fails if the `.mo` is missing or stale, so CI catches a forgotten recompile.

What this *can't* reach: `filter_horizontal`. Its widget builds labels in JavaScript from the `djangojs` catalog, and the admin's `jsi18n` view loads only `django.contrib.admin`'s own locale dirs — `LOCALE_PATHS` is ignored there, so three untranslated strings ("Choose %s by selecting them…") would render English. `WorkOrderAdmin` therefore uses the plain multi-select for `collaborators`; don't reintroduce `filter_horizontal` without checking that.

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

`inventory/tests.py` is now model-level only — the app has no views to drive. Everything that exercised a request path moved out with the stock pages; `workorders/tests.py` is where the app is actually covered.

The suite lost its one `TransactionTestCase` (`StockLockConcurrencyTests`) with the stock pages, and nothing replaced it. **`select_for_update()` did not go away with it** — `MachineUsage.save()` still takes a row lock on the old row before recomputing the `total_hours` delta ([workorders/models.py:86](workorders/models.py:86)), so there *is* a concurrent write path and it is covered only by plain `TestCase`s. A plain `TestCase` wraps the whole test in one transaction and hides the interleaving, so a lock-ordering regression there would pass CI. Any test that actually exercises two writers against `total_hours` has to be a `TransactionTestCase`.

Seed the material catalog, depot locations, machinery, and employee accounts from CSV (idempotent — safe to re-run; matches the `.env`/`.env.example` pattern, real files are gitignored):

```bash
cp seed_data/materials.example.csv seed_data/materials.csv
cp seed_data/locations.example.csv seed_data/locations.csv
cp seed_data/machines.example.csv seed_data/machines.csv
cp seed_data/users.example.csv seed_data/users.csv
# edit those four files with real data, then:
python manage.py seed_data
```

`machines.csv` takes `name,hourly_rate` — a machine's `total_hours` is never set by seeding, only accumulated by transformations that log usage against it. `materials.csv` takes `sku,name,unit_of_measure,category`.

The optional `hourly_rate` column is only written when the CSV actually carries a value for that row: omitting the column, or leaving the cell blank, **preserves** whatever is already in the database, so re-seeding never clobbers a rate someone set by hand in the admin. New records still fall back to the model default (`hourly_rate=NULL`). Clearing a value back to empty is an admin action, not a CSV one.

New users get a random temporary password printed to the console (share it securely); only `ADMIN`-role users get Django admin access — both `is_staff` (to log in) and `is_superuser` (to actually see/edit anything there; `is_staff` alone gets an empty "you don't have permission" admin index). Managers do not get admin access at all — what distinguishes them in the app is `is_manager_or_admin`: the Stroje nav entry, and an unrestricted view of everyone's hours and machine usage rather than just their own.

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

CI (`.github/workflows/ci.yml`) runs `ruff check` and `ruff format --check` (must pass, not just run), a `docker build` sanity check, and a test job as separate jobs on every push/PR. The test job is three steps, not one: `manage.py check`, then `manage.py makemigrations --check --dry-run`, then `manage.py test`. That middle step means **a model change without its migration file fails CI even if every test passes** — run `makemigrations` and commit the result alongside any `models.py` edit. Both triggers carry a `paths-ignore` list, and as of `30c6477` it is **`'**.md'` and nothing else** — a commit touching only Markdown runs **no jobs at all**, so don't wait for a green check on such a push and don't take one as evidence the code still builds. Everything else, dotfiles and `.github/` included, now triggers the full pipeline. Beware that the comment block above `on:` in `ci.yml` still describes the four removed dotfile globs (`.*`, `.*/**`, `**/.*`, `**/.*/**`) as if they were live; read the `paths-ignore` keys themselves, not the comment. Dependabot (`.github/dependabot.yml`) opens weekly update PRs for pip, Docker base images, and the Actions themselves.

## Deployment

Only the dev-oriented `docker-compose.yml` exists today (`runserver`, `DEBUG=True`, DB port exposed). A LAN-only production deployment to a company server is planned but not yet implemented — see [DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md) for the approach.

Both blockers that plan identified are now **fixed** (its Context section records the reasoning; sections 3-6 — prod compose file, `.env.production.example`, backup script, runbook — are still to do).

Static files are the part most likely to trip you up, because the correct behaviour differs per environment:

- **`WhiteNoiseMiddleware` is in `MIDDLEWARE` unconditionally**, in every environment including the test run — only the *storage backend* is environment-dependent. So "is whitenoise active?" is the wrong question; the one that matters is which of the two backends is live.
- **The static backend is chosen by `STORAGES['staticfiles']`, driven by the `STATICFILES_BACKEND` env var**, defaulting to plain `StaticFilesStorage`. It is *not* `STATICFILES_STORAGE` — Django removed that setting in 5.1, and it sat in this file being silently ignored until it was migrated. If you ever need to know which backend is really live, check it, don't read the setting: `from django.contrib.staticfiles.storage import staticfiles_storage; type(staticfiles_storage)`.
- **The default is plain storage specifically so the test suite works.** Whitenoise's manifest storage rewrites every `{% static %}` URL to a hashed name looked up in `staticfiles.json`, and raises when there's no manifest — non-strict mode doesn't save you either, since it falls back to hashing the file and that file isn't in `STATIC_ROOT` without a `collectstatic` run. Django's test runner forces `DEBUG=False`, and `DEBUG` is precisely what short-circuits the hashed-URL lookup. So flipping the default to manifest storage makes **every test that renders a template** fail with `ValueError: Missing staticfiles manifest entry`. Don't.
- **Production opts in through the `Dockerfile`,** which sets `ENV STATICFILES_BACKEND=...CompressedManifestStaticFilesStorage` and then runs `collectstatic`. Keep those two together: the `ENV` persists into the running container, so the backend that writes the manifest and the one that reads it can't drift apart. Setting the backend in `.env.production` instead would reintroduce exactly that split.
- **`static/` is source, `staticfiles/` is generated.** `.gitignore` used to ignore the former, which is why `static/img/background.jpg` — referenced by `base.html`'s inline `<style>` on every page — was missing from the repo for a while. New assets go in `static/` and must be committed.
- **Don't park non-assets under `static/`.** `collectstatic` publishes everything there at an unauthenticated `/static/` URL. `static/xlsx/` holds a working spreadsheet and is excluded in both `.gitignore` and `.dockerignore` for that reason; it would be better off outside `static/` altogether.
- **CI does not cover any of this.** The `docker build` job would catch a `collectstatic` failure, but the test job runs with plain storage and never requests a static URL. Verify static changes by running the built image with `DEBUG=False` and curling an asset.
