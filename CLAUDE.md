# CLAUDE.md

Guidance for Claude Code in this repository.

## Project

MaterialInventory records material **processing jobs** (what a job consumed and produced, who worked on it and for how long, which machines ran) and, as a separate kind of entry, **refuellings** (the fuel put into machines, and where it came from).

**It does not track stock.** There are no receipts, shipments, adjustments, balances or sufficiency checks. The name is historical. Don't reintroduce inventory semantics. The one quantity rule is *inside* a single job: consumed and produced totals must match (mass balance).

## Documentation

`docs/` explains in prose what this file states as rules. **A change that invalidates something here almost certainly invalidates something there too; update both in the same commit.**

| File | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Data model, job rules, reports, roles, the job-form scripts, the PWA |
| [docs/localization.md](docs/localization.md) | Czech locale: numbers, dates, `empty_label`, admin translation |
| [docs/development.md](docs/development.md) | Setup, tests, lint, seeding, CI |
| [docs/configuration.md](docs/configuration.md) | Env vars, HTTPS/proxy settings, static and media files, collation |
| [docs/deployment/DEPLOYMENT.md](docs/deployment/DEPLOYMENT.md) | Server runbook |
| [docs/user-guide.cs.md](docs/user-guide.cs.md) | End-user manual, **in Czech** |

Docs and code are English; only the user guide and the app's UI are Czech.

## Commands

```bash
uv sync                                                 # .venv from uv.lock, dev tools included
python manage.py runserver
python manage.py makemigrations && python manage.py migrate
python manage.py test --parallel                        # needs Postgres
python manage.py test workorders.tests.test_review.JobEditTests
python manage.py test materials.tests.MaterialModelTests.test_material_str
coverage run manage.py test --parallel && coverage combine && coverage report -m
ruff check . && ruff format .
shellcheck scripts/*.sh                                 # CI lint job runs this too
pre-commit install                                      # optional local mirror of CI lint + migration check
bash scripts/smoke_prod_stack.sh                        # CI docker-build job: full prod stack under a throwaway project
python manage.py seed_data                              # from seed_data/*.csv
python manage.py compilemessages -l cs --ignore=.venv
docker compose up --build                               # dev stack only
```

- **Commit a migration with every `models.py` change**, including label-only changes (`verbose_name` produces an `AlterField`). CI runs `makemigrations --check`.
- **CI runs `ruff format --check`**, so run `ruff format .` before pushing.
- **Dependencies are uv.** Direct ones are pinned exactly in `pyproject.toml`, everything else in `uv.lock` with hashes. Change them with `uv add` / `uv lock` and **commit `uv.lock` alongside**: the `Dockerfile` and CI run `uv sync --locked`, which fails on a stale lock. There is no `requirements.txt`.
- **`security.yml`** (pip-audit on the locked production dependencies, Trivy on the image, also weekly) is deliberately separate from `ci.yml`, so a new upstream CVE never blocks `deploy.yml`. Its Trivy action stays pinned to a commit SHA.
- A commit touching only `*.md` runs **no CI at all**, so a green check proves nothing about the code.
- Don't quote a test count in docs; it drifts.

## Tech stack and hard rules

Django 6, PostgreSQL, server-rendered templates, Czech mobile-first UI, Django admin as the back office. No API, no JS framework, no build step, no `package.json`, no CDN. (`htmx`, `widget_tweaks` and `rest_framework` were removed as unused; add them back only on purpose.)

**Scripts.** The app serves its own scripts from `static/` and never loads one from a third party. There are exactly three on the job form (the refuelling form uses the first two), plus `pwa.js` on every page:

- `static/js/job_rows.js`: adds and removes formset rows in the DOM. It hides taken choices only in the picker named by the section's `data-unique-field`.
- `static/js/searchable_select.js`: puts a type-to-filter box in front of every `<select>` in a row.
- `static/js/photo_picker.js`: one „Přidat fotku" button that keeps every photo picked.

**All three are progressive enhancement, and that is a hard constraint.** The test suite drives views through `self.client` with no browser, so the server path must be complete on its own. Never move behaviour into a script that the server cannot perform. With every script blocked, the form must still work:

- The row buttons are real submits (`add_<prefix>` / `remove_<prefix>`, and „= součet výstupů" `sum_consumed`), and the server answers them by re-rendering.
- The hidden `<select>` is still what gets posted. The search box has an `id` (`<select id>-search`) but **never a `name`**, and is capped at 40 characters on the client.
- Each file input works on its own.

The scripts build no markup of their own. They clone server-rendered `<template>`s (`formset.empty_form`, `data-photo-picker`, `data-photo-entry`) and read options, labels and `empty_label` off the rendered `<select>`. The scripts' own behaviour is untested by design. The tests cover only the contract they read off the page: `RowTemplateTests`, `SearchablePickerTests` and `PhotoPickerTests`. `RowTemplateTests` and `PwaTests` fail if a URL appears in a script.

**CSS.** All CSS lives in `static/css/app.css`. No inline `style=` attributes and no `<style>` blocks.

- **Colours.** Every colour is a custom property declared twice, once in `:root` and once under `@media (prefers-color-scheme: dark)`. A colour literal below those two blocks is a bug. `ThemeTokenTests` enforces this.
- **Dark mode** follows the OS setting only. The `theme-color` metas in `base.html` and the manifest's `theme_color` are kept in step with `--chrome-bg` by hand.
- **`button[hidden], .btn[hidden] { display: none }` is load-bearing.** `button, .btn` sets a `display`, which beats the UA `[hidden]` rule. The same applies to `.photo-field [hidden]` and to the `.combo` rules.
- **Desktop layout** is one `@media (min-width: 900px)` block at the end of `app.css`, over the same HTML; never sniff the user agent or fork templates per device. It must stay below the dark-mode block (`ThemeTokenTests` finds that by the first matching `@media`). Pages with tables set `{% block main_class %}wide{% endblock %}`; pages with the photo panel set `with-photo-panel`. The nav stays at the bottom on desktop too (the owner's choice).
- **Page-scoped CSS** uses `body.page-<url_name>`, so renaming a URL name drops that page's styles. Grep for `.page-` first. (None exist today.)

**PWA („VyrobaPK").** `workorders/pwa.py` serves three public URLs: `manifest.webmanifest`, `sw.js` and `offline/`. The manifest and service worker are templates in `templates/pwa/`, not static files. That way the icon URLs are hashed, and the worker is served from the root so its scope covers the whole site.

- **The worker does exactly one thing:** it answers a failed navigation with a cached anonymous offline page (fetched with `credentials: 'omit'`), plus network-first fallbacks for that page's CSS and logo. It caches no other page, ignores non-GET requests and queues nothing. Offline submission would be script-only behaviour, which the rule above forbids.
- **Updates.** `service_worker` embeds a hash of the cached URLs and of `offline.html`, so editing either reaches installed phones automatically.
- **Secure context.** Service workers need HTTPS or `localhost`.
- **Tests.** `PwaTests` covers this.

## Apps

- **`accounts`**: custom `User` with `role` (`WORKER`/`MANAGER`/`ADMIN`), `is_manager_or_admin` and `has_admin_access`. `role_required(*roles)` gates views (login redirect if anonymous, 403 if wrong role), and `REVIEWER_ROLES` lives beside it. Auth uses Django's generic views with the Czech forms in `accounts/forms.py`. There is no `views.py` or `urls.py`.
- **`materials`**: `Material` (SKU and name only; every quantity is implicitly tonnes, with the `t` hardcoded). It also hosts the `seed_data` command, which is the only place it imports `machines` or `locations`.
- **`machines`**: `Machine` (name, `is_active`, optional `hourly_rate` and `rate_per_ton`). It stores no totals. Also `FuelSource` (name, `is_active`): the refuelling's „Zdroj", a label managed in the admin like `Location`.
- **`locations`**: `Location` (name, `is_active`). One per job, and it is a **label**, not a stock location: nothing is summed or refused per location.
- **`workorders`**: everything else. `entry.py` holds `EntryPage`, the entry page both forms are built on (row and sum buttons, unbound rebuild, error banner, context, writing rows); `sections.py` holds the sections, each owning its rows end to end (`recorded_rows`, `row_form_kwargs`, `write`); `kinds.py` holds the two entry kinds, `JOB` and `REFUELLING`: what each entry *is* (model, form, sections, `own_fields`, templates, messages) plus a few hooks (`author`, `form_kwargs`, `edit_initial`, `page_errors`, `write_rows`, `extra_context`), and `page` / `is_valid` / `save` on top of `EntryPage`, along with `_review_on_submit` and `_approve`; `views.py` holds the generic `entry_create`, `entry_edit` and `entry_approve` (`urls.py` passes `{'kind': ...}`, URL names unchanged) and the job's read-only pages; `refuels.py` holds the refuelling's read-only pages; `reports.py` holds Souhrn, Stroje, Materiál, the CSV exports and the list helpers; `pwa.py` serves the PWA. All URLs are mounted at the root. Keep the reports a module of `workorders`, not a separate app (that was considered and rejected).

Catalog records are retired with `is_active=False` and protected from deletion by `on_delete=PROTECT`.

## The job

One submission writes a `WorkOrder` plus its rows, all in one `transaction.atomic()`:

- `StockMovement` line items, with a signed `quantity`: negative = `TRANSFORM_CONSUME`, positive = `TRANSFORM_PRODUCE`.
- `WorkerHours`, one row per participant.
- `MachineUsage`.
- `JobPhoto`.

Fuel is not part of a job; see **The refuelling**.

`collaborators` is **derived** from the hours rows, never entered: `WorkOrder.derive_collaborators` sets it to everyone with hours except the author, called by `WorkersSection.write` and by `WorkOrderAdmin.save_related` (the admin shows it read-only). Migration `0012_derive_collaborators` resynced the jobs the admin had let drift. `AdminCollaboratorsTests` and `DeriveCollaboratorsMigrationTests` cover this.

Rows carry **no timestamp of their own**; their date is their entry's `performed_on`. Every row cascades when its entry is deleted. `StockMovement` and `MachineRefuel` are not registered in the admin; they are edited as inlines of `WorkOrderAdmin` and `RefuellingAdmin` (`AdminIndexTests` checks both). There a line item's sign must match its type, consumed negative and produced positive, never zero (`MovementAdminForm`, `AdminMovementSignTests`).

**Mass balance.** A job needs at least one consumed row and one produced row, and `sum(consumed) == sum(produced)` must hold exactly (`Decimal`, no tolerance). It is `JobKind.page_errors`, checked before the transaction. The error totals go through `formats.localize`. **The admin doesn't check it, on purpose**: it is the back office's way around the rule, and Django's admin has no clean place to show an error spanning two inlines. The next correction on `job_edit` enforces it again.

**Required fields.** „Popis", „Lokace" and „Moje hodiny" are required on `WorkOrderForm` itself; there is no exemption (the old fuel-only job is gone). Valid rows are still balance-checked when a job field fails, so both errors show (`ErrorBannerTests`). `Kind.is_valid` runs every kind's `page_errors` this way, so a refuelling with no machine shows that beside a field error too.

**No duplicates per section.** Each section may name the same material, machine or person only once. `UniqueChoiceFormSet` handles this in two ways:

- **On submit**, `clean()` puts the error on the repeated row.
- **On unbound renders only**, `_hide_taken_choices()` drops other rows' picks from each dropdown. Doing it on a bound formset would turn the real error into „Vyberte platnou možnost.". Each row keeps its own choice.

The rule is per section, so one material may be both consumed and produced. `unique_field` names the one picker the rule is about; a refuel row's „Zdroj" is not unique. Server-rendered rows have taken options excluded from the DOM, while script-added rows carry the full catalog. That asymmetry is expected; don't remove the server-side narrowing. `DuplicateRowTests` covers this.

**Text fields.**

- `description` („Popis") is required. It is capped at `DESCRIPTION_MAX_LENGTH` (40) on the form, against `max_length=255` on the model.
- `notes` („Poznámky") is an optional `TextField`, capped at `NOTES_MAX_LENGTH` (2000). It appears only on `job_detail`.
- A form tighter than its model is fine and needs no migration.
- `WorkOrderAdminForm` repeats the description cap so admin-saved jobs remain editable on `job_edit`.

`DescriptionAndNotesTests` covers this.

**Number bounds.** The number columns are `decimal(12, 2)`, except `StockMovement.quantity`, which is `decimal(12, 3)` (the forms still take 2 places). A form field wider than its column turns a big value into an unhandled `numeric field overflow` 500. So every number box has a `max_value`, set from constants in `forms.py`:

| Constant | Value | Applies to |
|---|---|---|
| `WORKER_HOURS_MAX` | 24 | worker hours |
| `METER_READING_MAX` | 99 999 | motohodiny |
| `TONS_MAX` | 99 999 | tonnage |
| `LITRES_MAX` | 2 000 | litres (Tankování) |

- The limits are whole numbers because Django's error message prints them unlocalised.
- `CappedInline` in `admin.py` reads each inline box's rules (limits, step, decimal places) off the entry form's own field, so whatever the admin saves, `job_edit` and `refuel_edit` accept. Trailing zeros don't count as decimal places, because the admin re-posts a stored 2 as 2.00. `AdminNumberRulesTests` covers this.

`ValueLimitTests` covers this.

**Machine row** (stroj, motohodiny, tuny). Only the machine is required; `MachineUsageForm.check_row` refuses a number with no machine. A typed `0` in either box counts as blank (`_zero_is_blank`). A machine row always writes a `MachineUsage`, with blank numbers as `NULL` (unknown).

`MachineUsage.hours` is the **hour-meter reading** („Stav motohodin"), not time run. `MachineUsage.tons` is not part of the mass balance, because chained machines each process the same material.

**Four unrelated numbers.** Never derive one from another:

- `WorkerHours.hours`: labour.
- `MachineUsage.hours`: meter reading.
- `MachineUsage.tons`: throughput.
- `MachineRefuel.litres`: fuel.

**Photos.** A job has 0 to `PHOTO_MAX_COUNT` (10) `JobPhoto` rows. New uploads are always added, never replacing a stored photo. A photo goes away only through „Odebrat" on `job_edit`, or when its job is deleted.

Processing:

- `clean_photo_upload` checks the extension and `PHOTO_MAX_BYTES` (10 MB) before decoding.
- `photos.shrink_photo` (Pillow + `pillow-heif`) applies the EXIF orientation, fits the image within 1600 px and saves it as JPEG quality 80, which also strips EXIF.
- `WorkOrderForm._clean_photos` checks the count (including kept photos) and `PHOTO_MAX_TOTAL_BYTES` (20 MB) before decoding anything. Keep 20 MB below the `Caddyfile`'s `request_body max_size 25MB`, so oversize batches get a Czech error instead of a 413.
- The admin's `JobPhotoForm` goes through the same function.

Easy to break:

- **Two inputs.** `photo` (gallery, `multiple`) and `camera_photo` (`capture="environment"`, no `multiple`). Chrome on Android offers no camera from a bare `accept` input. `clean()` merges both into one list.
- **`enctype="multipart/form-data"`** belongs on each caller's `<form>` tag. The shared partial can't supply it.
- **File inputs lose their picks on every re-render**, so the photo field renders last, with a hint. Stored photos survive re-renders via the `remove_photos` checkboxes; the no-JS rebuild reads them back with `getlist`.
- **Files are deleted by a `post_delete` receiver**, deferred to `transaction.on_commit`. Tests asserting that a file is gone need `captureOnCommitCallbacks`. `WorkOrderAdmin.save_formset` handles the one case the receiver can't see: an admin inline swapping the file on a surviving row.
- **Photos are never public.** `MEDIA_URL` routes to `protected_media` (`role_required(MANAGER, ADMIN)`); neither whitenoise nor Caddy serves `MEDIA_ROOT`.
- **Never link the bare file**: an installed app has no address bar, so it was a dead end. Phones follow „Fotka n" to `job_photo` (`/jobs/<pk>/fotky/<n>/`). On desktop, `job_detail` and `job_edit` pin every photo in `_photo_panel.html` at the right, switched by `#fotka-<n>` anchors and `:target` CSS, with no script and no reload (a reload would lose `job_edit`'s input). The panel is `position: fixed`, not `sticky`, or each jump scrolls the page. Each „Fotka n" renders twice (`_photo_links.html`), one hidden per width. Full size is the `:target` overlay `_photo_full.html`.

`JobPhotoTests` covers this.

**Dates.**

- `performed_on` (required, defaults to today, no future dates) drives every report, filter and „Provedeno" column. Its widget needs `format='%Y-%m-%d'`, or the `cs` locale renders a value `<input type="date">` shows as blank.
- Lists order by `[-performed_on, -created_at]`. The exception is `my_jobs`, which orders by `-created_at` because it is a list of recent submissions.
- `created_at` is "when typed in".

**Approval.** A worker's job or refuelling is saved `PENDING`; a manager's or admin's own is saved `APPROVED` with themselves as reviewer. **Every report counts only `APPROVED` entries.**

- **Review actions.** There are exactly three: approve, edit, delete. There is no "returned" status. Editing does not approve.
- **Worker feedback.** `my_jobs` (`_my_recent_jobs`, top of Souhrn, last `MY_JOBS_LIMIT` = 5) is a worker's only view of a job's review status. It is scoped by `created_by` only, ignores the filter form, and is empty for managers. `my_refuellings` does the same for refuellings, under the Tankování form.

**Recording for someone else.** Managers and admins get an `author` field („Zapsat za"). The job then belongs to the author, but approval follows the submitter. `JobKind.author` resolves it from the POST (`_submitted_author`) before any formset is built.

`collaborator_queryset(user, viewer)` takes two people:

- `user` is the author, and is excluded from the list.
- `viewer` is whoever fills in the form. A plain worker viewer may name only workers.

**Editing (`job_edit`).** It goes through the same `JOB` kind as the submission (`page`, `is_valid`, `save`), so corrections are mass-balanced too. The author's own hours are the job's field, written by `JobKind.write_rows`; the workers section holds only the collaborators. Rows are written as the author, and the collaborator list uses (author, editing manager).

It must also widen the catalog pickers to the retired records the job already names. Otherwise a row renders blank and saving silently deletes it. The mechanism: each section's `row_form_kwargs` derives `keep` from its `recorded_rows` and it reaches `_offer_recorded`; `keep_location` does the same for the location. `RetiredCatalogOnEditTests` covers this.

## The refuelling

`Refuelling` is its own entry, not part of a job: optional `notes`, `performed_on` (same `EntryForm` field and rules as the job's) and `MachineRefuel` rows (machine, `source` → `FuelSource`, `litres`), written in one `transaction.atomic()` by `entry_create` with the `REFUELLING` kind (`refuel_create`, `/refuel/`).

- **Rows** are a machine and litres, or blank (`RefuelRowForm.check_row`); `source` is optional, on the form and in the column. At least one row (`RefuellingKind.page_errors`, `NO_ROWS_ERROR`), one row per machine (the section's `picker`). `litres` is 0,01 to `LITRES_MAX` and not nullable. Fill-ups migrated from jobs have no source.
- **Same machinery as the job form.** `EntryPage` builds the page (row buttons, unbound rebuild, error banner), `_row_sections.html` renders the section. No photos, no „Zapsat za".
- **Review** is the job's: `ReviewStatus`, saved by `_review_on_submit`, approved by `_approve` (both in `kinds.py`), three actions (approve, edit, delete) under `/refuels/`, all `role_required(MANAGER, ADMIN)`. `refuel_edit` widens its pickers with `keep` (machines) and `keep_source`, both from `RefuelSection.row_form_kwargs`. Only approved refuellings count on Stroje.
- **Migration `0010_move_fill_ups`** moved each job's fill-ups onto a refuelling with the job's date, author and status. Emptied fuel-only jobs were deleted, except ones with photos.

`RefuellingFormTests`, `RefuellingReviewTests` and `MachineRefuelReportTests` cover this.

## The job form

`_job_form_fields.html` holds the whole form body: job fields plus the four sections from `JOB_SECTIONS`. Both `transform_form.html` and `job_edit.html` include it. Only the heading, the submit button, the cancel link, the `<form>` tag and the CSRF token stay in the callers. The section loop (and the two row scripts) is `_row_sections.html`, shared with the refuelling form's `_refuel_form_fields.html`; everything below applies to both forms.

- „Zapsat za" is behind `{% if order_form.author %}`.
- The hours label is read off the form (`job_edit` sets „Hodiny – <author>").

**Every error must render.** Nothing is written unless all five forms validate, so an error with nowhere to render looks like a button that did nothing.

- Row errors go through `_row_errors.html`; section errors render via `non_form_errors`.
- A refused submission also gets one red banner from `EntryPage.is_valid()` (`_form_error_summary`). It reads „Vyplňte prosím všechna povinná pole." if any error has code `required`, otherwise the generic text.
- `RowForm.clean()` returns early when a field already failed, so the incomplete-row message never covers the real error.

`ErrorBannerTests` covers this.

**Row buttons.** `EntryPage` checks `_pressed_row_button` before treating the POST as a submission, then rebuilds every form **unbound** from the raw POST, with that section one row bigger or smaller. Unbound means no errors and no balance check on a half-filled form.

- Removing takes the **last** row.
- Sections are clamped between `MIN_ROWS_PER_SECTION` (1) and `MAX_ROWS_PER_SECTION`, and `TOTAL_FORMS` from the POST is untrusted.
- Both buttons need `formnovalidate`.
- At the floor, „− odebrat řádek" is rendered with `hidden` (not omitted), so the script can unhide it.
- `job_row_formset` builds a class per call, because `extra` is a class attribute.
- Each `<form>` opens with an off-screen decoy submit, so Enter saves instead of adding a row.

`RowButtonTests` covers this.

**Sum button („= součet výstupů", `sum_consumed`).** Screening jobs weigh only what comes out, so Spotřebováno gets a button that fills its row's quantity with the Vyrobeno total. It shows only while the section has exactly one row (`hidden` otherwise, like „− odebrat řádek"). It is declared on the section (`Section.sum_from` / `sum_field`) and takes the row-button path: a `formnovalidate` submit answered by the same unbound rebuild, with `_summed_rows` filling the row. Unparsable values, and any above the box's `max_value` (`_rows_total`; one past Decimal's range used to overflow into a 500), are skipped, as they are for the tonnage mirror, and the total is written with `format(..., 'f')`, never as an exponent. `job_rows.js` sums in hundredths in the DOM. `SumButtonTests` covers this.

**Tonnage mirror.** Each machine row's „Tuny" defaults to the Spotřebováno total (`Section.mirror_from` / `mirror_source` / `mirror_field`), only on rows that name a machine. It is a default, not a derivation; a typed value is kept. The server fills blank boxes on every unbound rebuild (`_mirrored_rows`, after any sum), never on a real submission. `job_rows.js` does it live, following a box while it is blank or still holds the last copied total. `TonnageMirrorTests` covers this.

## Reports

Souhrn, Stroje and Materiál each have a filter card, a summary table, and the detail rows behind it. Stroje has two summary/detail pairs: usage and fuel.

**Every report table, and both Přehled lists, start at `PREVIEW_ROWS` (5) rows.** `_previews` / `_preview` cut each one, and `_show_all.html` renders „Zobrazit vše (n)", a plain link that adds the table's key to `?vse=` (`EXPAND_PARAM`), keeps the filters and other expanded tables, and jumps back to the card (`#tabulka-<key>`). Once expanded (and longer than 5) it renders „Zobrazit méně" instead, which removes only that key and, for a paginated table, its page param. Pass a paginated table as its `_paginated` dict, so `_preview` knows the param. Templates loop over `tables.<key>.rows`, not the raw context. A paginated table shows its page links only once expanded, and they carry `vse`. Exports ignore all of this. `ShowAllTests` covers this.

- **Stroje** (`machine_dashboard`): a manager page.
  - Per machine: latest reading (`Max`), hours run, tonnage, both rates, and three money columns from `_machine_costs`. That makes `MachineReport.summary` a list rather than a queryset.
  - **Motohodiny are readings, never summed.** `_with_run_hours` computes each row's run time as its reading minus the previous approved reading of that machine. "Previous" is by value with a pk tie-break, the baseline is all approved rows (not just filtered ones), and a first or blank reading is `None`. „Cena za hodiny" prices the summed run hours. `MachineReadingTests` covers this.
  - **Unknown is not zero.** An unset rate or unknown tonnage is „—"; a priced machine that didn't run costs `0,00`. „Celkem" is unknown if any priced side is (`filtered_usages` tells the two cases apart).
  - The fuel card (`MachineReport.fuel_summary`) stays a queryset, because its zeros are real. It reads approved refuellings through `RefuelFilterForm` (Stroje's filter, dated and authored by the refuelling). „Detail tankování" shows each fill-up's source and links to its refuelling.
  - Every active machine is listed; naming one in the filter narrows the table to it.
  - The two tables paginate independently (`_paginated(request, rows, param)`). `MachineRefuelReportTests` covers this.
- **Materiál** (`material_dashboard`): a manager page; the only place line items are read across jobs. It annotates consumed (`Abs`), produced and net (`Coalesce`); zeros are real. Its filter has `location` but no `created_by`. Its „Druh pohybu" (`movement_type`) narrows only Detail položek and its export (`MaterialFilterForm.detail_rows`), never the summary, whose columns already split the two directions.
- **Souhrn** (`time_worked`, nav „Souhrn", URL still `/hours/`): any user.
  - **Odpracované hodiny** sums `WorkerHours` per person. It re-queries by `pk__in` before aggregating, because the collaborator join would otherwise multiply the `Sum`. Workers see only their own rows (see Access); a manager filtering to one worker sees only that worker's row, not the colleagues on their jobs (`HoursReport.subject`).
  - **Stroje** („Hodin za období", „Tun") and **Tankování** (`_fleet_totals`) are for workers only (`fleet` is `None` for managers, who have Stroje). They are **deliberately unscoped**: every approved entry counts. They read a `MachineReport` built from only the page's dates (`machine`/`created_by` in the querystring are ignored), and have no export. `SouhrnFleetTests` covers this.
- **Přehled** (`job_dashboard` and the `/jobs/` pages): a manager page. Location is shown and filtered only here and on Materiál. It links to **Přehled tankování** (`refuel_dashboard`) with the pending refuelling count.

**CSV exports** sit at `<page>/export/`:

- summaries: `time_worked_export`, `machine_dashboard_export`, `machine_refuel_export`, `material_dashboard_export`;
- detail rows: `material_detail_export`, `machine_refuel_detail_export`, `machine_usage_detail_export`.

Rules:

- Each export reads **the same report class as its page** (`MachineReport`, `MaterialReport`, `HoursReport`), so it can't disagree with it. A report is built from the filter data (plus the user, for `HoursReport`), applies approved-only and the invalid-filter rule once, and computes each table on first use.
- Each export carries its page's gate. The worker scoping lives in `HoursReport`.
- A detail export contains every filtered row, not just the current page.
- `_csv_response` writes `;`-delimited UTF-8 with a BOM, which is what Excel needs under `cs`. Numbers go through `number_format`. Unknowns are blank cells (`_csv_number`), never `—`. Dates are ISO.

`TableExportTests` covers this.

## Access

- **The nav** (in `base.html`, hardcoded): everyone sees Zpracování, Tankování and Souhrn; Přehled, Stroje and Materiál sit inside `{% if user.is_manager_or_admin %}`. Hiding a link is not access control: those views are also gated with `role_required(MANAGER, ADMIN)`. Keep nav labels short, since a manager has six entries.
- **Worker scoping** happens in the queryset, not by hiding fields. Only `time_worked`'s labour tables need it (its fleet tables are shared on purpose). It applies `participation_filter`, and it also limits the summary's `WorkerHours` to the requesting user before summing (`HoursReport.subject`, the same cut a manager's „Pracovník" filter makes). Deleting the `worker` filter field is cosmetic (`test_worker_cannot_bypass_restriction_via_worker_param`). Don't add scoping to `role_required` views.
- **Filter forms** subclass `DateRangeFilterForm` and declare `lookups` / `date_lookup`; views call `form.filter(qs)`. Unbound shows everything; **invalid returns `.none()`**.
- **List pages.** A filtered page needs `**_list_page_context(request, rows)` (`page_obj`, `querystring`, `date_presets`) plus the `_filter_card.html` and `_pagination.html` includes. Date presets are plain links.
- **The `/admin/` gate.** `AdminSessionRequiredMiddleware` returns **404** for everything under the admin prefix unless `has_admin_access` (`is_active and is_staff and (is_superuser or role == ADMIN)`). That covers `/admin/login/` too, because the server is public. Details:
  - **It must sit above `CommonMiddleware`**, or `APPEND_SLASH` turns `/admin` into a revealing 301. It also sits above CSRF and auth middleware, so it resolves the user itself.
  - It is a 404, deliberately not a redirect.
  - The `base.html` admin link checks the same property.
  - Tests: `AdminGateTests`, `AdminLinkTests`. The smoke script also checks `/admin` without the slash.
- **Superusers bypass role checks** in `role_required`, `is_manager_or_admin` and `has_admin_access`, because `createsuperuser` never sets a role.
- **`auth.Group` is unregistered**: permissions are unused.

## Locale

UI copy is **hardcoded Czech**, not `gettext`. Don't introduce `{% trans %}`. `LANGUAGE_CODE='cs'` translates Django's own strings; `LocaleMiddleware` is not installed. Traps:

- **Templates render `12,50`**; tests asserting on rendered numbers need the comma. Form inputs stay unlocalised (`localize=False` keeps `type="number"` and the numeric keypad). Code outside templates must localise itself (`localize`, `number_format`).
- **An unbound date field with a `date` initial** renders `dd.mm.yyyy`, which `<input type="date">` shows as blank. Give it `format='%Y-%m-%d'`.
- **Every `ModelChoiceField` needs an explicit `empty_label`.** Django's default is untranslated English.
  - Row pickers use the bare noun (`Materiál`, `Stroj`, `Pracovník`, `Zdroj`).
  - Filters use an "all" phrase (`Všechny stroje`, …).
  - Row number boxes use a unit placeholder plus `title`.
  - `EmptyLabelTests` covers this.
- **Template dates** use explicit formats (`|date:"Y-m-d H:i"`). `USE_TZ` is on; Python code formatting a datetime needs `timezone.localtime()`.
- **The admin is Czech via three mechanisms**, and a new model needs all three:
  - `AppConfig.verbose_name`;
  - `verbose_name` on every model and field;
  - `locale/cs/LC_MESSAGES/django.po` for Django msgids Django never translated (Django msgids only).

  Commit both `.po` and `.mo`. `AdminCzechTests` fails on a stale `.mo`. Don't use `filter_horizontal`: its JS strings can't be translated.

## Tests

One `tests.py` per app, except `workorders/tests/`, which holds almost everything and is a package split by topic (`test_submission`, `test_job_form`, `test_review`, `test_reports`, …). Shared fixtures (`job_payload`, `default_location`, `ReviewFixtureMixin`) live in `workorders/tests/helpers.py`; new modules must be named `test_*.py`. Plain `TestCase`, no pytest, no mocking. Tests drive views through `self.client` and assert on context and on database rows.

- Postgres is required, and there is no test settings module, so `settings.py` changes affect the suite.
- Use `force_login`, **never `Client.login()`**. The axes backend rejects `authenticate()` without a request. `AxesStandaloneBackend` defines no `get_user`, which is what keeps `force_login` working.
- There are no concurrent writes, so no `TransactionTestCase`. Add a read-modify-write and you'll need one.
- CI runs `--parallel`: one cloned database per process. Nothing may share state outside the database (`JobPhotoTests` uses its own temporary `MEDIA_ROOT`). Coverage writes per-process files, so always `coverage combine` before `report`.

## Deployment

The production server is a public VPS behind the company domain, over HTTPS. `docker-compose.prod.yml` runs `db`, `web` (gunicorn) and `proxy` (Caddy, ports 80/443). `docker-compose.yml` is dev only and **must never run on the server**, because it publishes Postgres. Deploys go through `scripts/deploy.sh`, either by hand or via the manual `deploy.yml` workflow. It does: backup, fast-forward, build, `migrate` from the new image, then swap. It refuses rollbacks.

Easy to break:

- **`--env-file .env.production`** on every prod compose command (`env_file:` doesn't feed `${...}` interpolation). The project name is `materialinventory-prod`, so it doesn't share the dev volumes.
- **No `ports:` on `db` or `web`.** Docker's published ports bypass `ufw`, and `SECURE_PROXY_SSL_HEADER` is safe only because nothing but Caddy can reach gunicorn.
- **`FORWARDED_ALLOW_IPS: "*"`** on `web`. Without it, gunicorn drops `X-Forwarded-Proto` and `SECURE_SSL_REDIRECT` loops forever. (The smoke test doesn't catch its removal.)
- **`CSRF_TRUSTED_ORIGINS=https://<domain>`** is required behind TLS termination; otherwise every POST is a 403 while pages still load.
- **HTTPS settings default to off**, because the test runner forces `DEBUG=False`. Production opts in via `.env.production`. `check --deploy` must report exactly one warning (`W021`).
- **Never `down -v` in prod.** `caddy_data` holds the certificate (Let's Encrypt allows 5 issues per host per week) and `media_data` holds every photo. Remove `materialinventory-prod_db_data` by name instead.
- **Postgres collation is ICU `cs-CZ`**, set by `POSTGRES_INITDB_ARGS` and read only on an empty volume.
- **Static files.** `STORAGES['staticfiles']` follows `STATICFILES_BACKEND`, which defaults to plain storage so tests work without `collectstatic`. The `Dockerfile` sets the manifest backend next to `collectstatic`; keep those two lines together. `static/` is source (commit new assets); `staticfiles/` is generated. Never put non-assets under `static/`: `collectstatic` publishes them.
- **Media files.** `MEDIA_ROOT` is the `media_data` volume and is never served directly. `backup_db.sh` doesn't cover it.
- **Login rate limiting** is `django-axes`, locking by **username** (5 failures, 30 minutes), because the depot shares one NAT address. Details:
  - `axes.W006` is silenced on purpose.
  - The admin section is off (axes has no Czech).
  - `lockout.html` quotes "30 minutes" in prose, so change it together with `AXES_COOLOFF_TIME`.
- **Known gap:** `seed_data` prints temporary passwords that are now internet-facing. It is documented in DEPLOYMENT.md; don't close it silently.
- **Logging.** `LOGGING` sends `django.request` errors to stdout at `ERROR`, or prod 500s are logged nowhere.
- **Line endings.** `.gitattributes` keeps `*.sh` LF. `.env.production` has its own `.gitignore` entry.

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one root `GLOSSARY.md` and `docs/adr/`. See `docs/agents/domain.md`.
