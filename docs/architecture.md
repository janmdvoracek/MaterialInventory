# Architecture

One Django project (`config`) with five apps. The four catalog apps hold reference data; `workorders` holds the jobs and refuellings, every page and every URL.

```
accounts ───┐
materials ──┤
machines ───┼──> workorders
locations ──┘
```

The catalog apps don't know about each other. The one exception is the `seed_data` command in `materials`, which loads all five CSVs.

> **This app does not track stock.** There are no receipts, shipments, balances or sufficiency checks. A job records what a worker says happened. If balances are ever wanted back, that is a new subsystem, not old wiring to re-enable.

---

## Data model

There are two kinds of entry. A job is one `WorkOrder` and the rows hanging off it:

```
WorkOrder #17  "Drcení kameniva"   location: Lom, performed_on: 2026-09-02
├── StockMovement  TRANSFORM_CONSUME  −20 t  Kámen
├── StockMovement  TRANSFORM_PRODUCE  +14 t  Frakce 8/16
├── StockMovement  TRANSFORM_PRODUCE   +6 t  Frakce 4/8
├── MachineUsage   Drtič  reading 12 345, 20 t
├── WorkerHours    novak 6 h, svoboda 4 h
├── JobPhoto       ×2
└── collaborators  [svoboda]
```

A refuelling is one `Refuelling` and its fill-ups:

```
Refuelling #5   performed_on: 2026-09-02, notes: "…"
├── MachineRefuel  Drtič     source: Nádrž dílna      40 l
└── MachineRefuel  Bagr CAT  source: Nádrž dílna      65 l
```

| Model | One row is | Notes |
|---|---|---|
| `StockMovement` | one material line | `quantity` is **signed**: negative = consumed, positive = produced. Always tonnes. The name is historical. |
| `WorkerHours` | one person's labour on the job | One per person per job (`UniqueConstraint`). |
| `MachineUsage` | one machine that ran | `hours` is the **hour-meter reading**, not run time. `hours` and `tons` are nullable, meaning unknown. |
| `MachineRefuel` | one machine's fill-up, on a `Refuelling` | `litres`, not nullable. `source` (a `FuelSource`) is optional. |
| `JobPhoto` | one uploaded photo | Up to 10 per job. |

Shared rules:

- **Rows have no timestamp of their own.** Their date is their entry's `performed_on`. `job_edit` and `refuel_edit` delete and rewrite rows, so an insert time would drift to the day of the correction.
- **Every row cascades when its entry is deleted**, so `job_delete`, `refuel_delete` and the admin's delete are all a plain `.delete()`.
- **`collaborators` is derived** from the hours rows (`collaborators.set(worker_hours.keys())`). The admin is the one place they can drift apart, because it edits the M2M and the hours inline independently.
- **`StockMovement` and `MachineRefuel` are not in the admin index.** They are edited as inlines of `WorkOrderAdmin` and `RefuellingAdmin`, so everything about an entry is edited in one place.
- **`Machine` stores no totals.** Every figure on Stroje is an annotation over approved rows.

**Four numbers, all independent.** Never derive one from another:

| Number | What it is |
|---|---|
| `WorkerHours.hours` | labour hours a person typed |
| `MachineUsage.hours` | meter reading |
| `MachineUsage.tons` | throughput of that one machine |
| `MachineRefuel.litres` | fuel |

Machine tonnage is **not** part of the mass balance. Chained machines each process the same material, so the machine rows add up to a multiple of the job's total.

Fuel is not part of a job. Refuelling happens on its own schedule, often for several machines at once and from a known tank or station, so it is its own entry (`Refuelling`) with its own review. It never touches the mass balance or a job's fields.

## Recording a job

`transform_create` (Zpracování, the landing page) collects everything in one form and writes it in one `transaction.atomic()`. Nothing is written unless every form validates and the mass balance holds.

### Mass balance

A job needs **at least one consumed row and one produced row, and their totals must be exactly equal.** The comparison is of totals, not row against row, because one input is normally crushed into several fractions. Equality is exact `Decimal` (`5.00` equals `5`; `5.01` does not), and the form accepts two decimals.

The check needs no database state, so it runs in the view before the transaction. The error message localises its totals (`9,5`, not `9.5`).

### Required fields

„Lokace", „Popis" and „Moje hodiny" are required on the form itself (`WorkOrderForm`). Every job records work, so there is no exemption. When a job field is missing and the rows are valid, the balance is still checked, so both errors show at once.

### One row per thing

Each section may name a material, machine or person **once**. Two rows for the same thing are a quantity that should have been typed once, and they would double-count in reports. The rule is per section, so one material may be both consumed and produced.

`UniqueChoiceFormSet` (with `MaterialRowFormSet`, `MachineRowFormSet`, `WorkerRowFormSet` and, on the refuelling form, `RefuelRowFormSet`) does two things. Its `unique_field` names the one picker per row the rule is about:

- **`clean()`** refuses a repeat, with the error on the repeated row.
- **`_hide_taken_choices()`** removes other rows' picks from each dropdown, on **unbound** formsets only. On a bound one, a duplicate would fail as „Vyberte platnou možnost." instead of the real message. Each row keeps its own pick.

Server-rendered rows have the taken options removed from the DOM, so freeing one up can't bring it back. Rows added by `job_rows.js` carry the full catalog and hide options live. The asymmetry is harmless; removing the server-side narrowing would lose the feature for anyone without the script.

### Text fields

| Field | Shape | Where it shows |
|---|---|---|
| `description` („Popis") | required one-liner, `DESCRIPTION_MAX_LENGTH` = 40 | the „Zakázka" column on four reports, and the Materiál detail CSV |
| `notes` („Poznámky") | optional textarea, `NOTES_MAX_LENGTH` = 2000 | `job_detail` only, through `linebreaksbr` |

Both caps are tighter than the model, which needs no migration. `WorkOrderAdminForm` repeats the description cap; otherwise a longer value saved in the admin would be refused next time on `job_edit`. Old rows may have an empty description, which renders as „—".

### Number limits

The number columns are `decimal(12, 2)`. A form field wider than its column turns a large value into an unhandled `numeric field overflow` 500. So every number box has a `max_value`, rendered as the input's `max`:

| Constant | Limit | Boxes |
|---|---|---|
| `WORKER_HOURS_MAX` | 24 | „Moje hodiny" and collaborator hours (a job is one day) |
| `METER_READING_MAX` | 99 999 | „Stav motohodin" |
| `TONS_MAX` | 99 999 | material quantity and machine tonnage |
| `LITRES_MAX` | 2 000 | „Natankováno" on the Tankování form |

The limits are whole numbers because Django's „menší nebo rovna …" message prints the limit unlocalised. `CappedInline` in `workorders/admin.py` repeats them on the admin inlines (signed for line items). `ValueLimitTests` covers this.

### The machine row

The *Použité stroje* row has three fields: stroj, motohodiny, tuny. Only the machine is required, so `MachineUsageForm` overrides `check_row` instead of following `RowForm`'s all-or-nothing rule:

| Row | Result |
|---|---|
| all blank | nothing |
| machine, with or without hours/tons | a `MachineUsage` (blank numbers unknown) |
| a number with no machine | refused |

A typed `0` in either box counts as blank (`_zero_is_blank`). A stored `0` would read as "ran for nothing".

### Location

`WorkOrder.location` points to a `Location` catalog entry. It is one per job, required on the form, nullable (jobs from before the column, and the few fuel-only jobs with photos that migration `0010` kept, have none) and `PROTECT`ed. It is a **label**: nothing is summed or refused per location.

It is shown and filtered on Přehled and Materiál only, plus `job_detail` and the Materiál detail CSV. It is a plain `<select>`, not a searchable one.

### Dates

`performed_on` is the day the work was done, and `created_at` is when it was typed in. The field is required and defaults to today (`initial=timezone.localdate`); empty or future dates are refused.

- **The widget needs `format='%Y-%m-%d'`.** Without it, the `cs` locale renders `02.09.2026`, which `<input type="date">` shows as blank.
- **Every report filters, sorts and displays by `performed_on`.** Lists order by `['-performed_on', '-created_at']`, both in the views and in `WorkOrder.Meta`. Row-level pages filter on `work_order__performed_on`.
- **`my_jobs` is the exception**, ordered by `-created_at`. It is a list of recent submissions, so a job just back-dated to last month must appear at the top.

### Photos

`JobPhoto` rows are optional evidence. No report, filter or export reads them, and they appear only on `job_detail` (and `job_edit`), as „Fotka 1", „Fotka 2", … links and, on desktop, in a pinned panel.

- **Never a link to the file.** The bare file is a page with no app chrome, and an installed app has no address bar (and an iPhone no back gesture), so it was a dead end.
- **Phones: `job_photo`.** `/jobs/<pk>/fotky/<n>/` renders the one photo inside `base.html`, with „← Zpět na zpracování" and „Předchozí fotka" / „Další fotka" buttons. `n` is the 1-based position the job pages label it with; one the job doesn't have is a 404.
- **Desktop: the photo panel.** From 900px, `job_detail` and `job_edit` with photos pin `_photo_panel.html` at the right (`{% block aside %}`, `main.with-photo-panel`), so a reviewer checks the entry against its photos side by side. It holds every photo as a `#fotka-<n>` section with `loading="lazy"`; CSS shows the first, or whichever is the URL's `:target`, and the buttons are anchor links. That switches photos without a script and without a reload, which on `job_edit` would throw away what the manager has typed. The panel is `position: fixed`, not `sticky`: a jump to a sticky element scrolls the page back to it. Each „Fotka n" is rendered twice (`_photo_links.html`), a `job_photo` link for phones and a `#fotka-<n>` link for desktop, one hidden per width.
- **Fixed-height frame, full size on tap.** Both viewers put the photo in `.photo-frame`, a fixed-height box with `object-fit: contain`, so the buttons don't move between photos. A lone button keeps its half (a two-column grid). Tapping the photo opens `_photo_full.html`, a full-screen overlay at natural size, shown while it is the `:target` (`#fotka-<n>-plna`) and closed by „Zavřít".

- **Always added, never replaced.** Every upload, on a new job or on `job_edit`, becomes a new row. A photo goes only via a ticked „Odebrat" box on `job_edit`, or with its job.
- **Shrunk before storing**, because the disk is 20 GB and a phone photo is 2–6 MB. `clean_photo_upload` checks the extension (`PHOTO_EXTENSIONS`) and `PHOTO_MAX_BYTES` (10 MB), then `photos.shrink_photo` takes over:
  - decodes with Pillow plus `pillow-heif` (iPhone HEIC);
  - applies EXIF orientation;
  - fits within 1600 px;
  - re-encodes as JPEG quality 80, which also drops EXIF and GPS.

  An undecodable file is a field error naming the file.
- **Checked together first.** `WorkOrderForm._clean_photos` checks the count (stored minus removed plus new, at most `PHOTO_MAX_COUNT`) and the total (`PHOTO_MAX_TOTAL_BYTES`, 20 MB) before decoding anything. The 20 MB sits below the `Caddyfile`'s 25 MB `request_body` cap, so a refused batch gets a Czech error instead of a 413.
- **The admin goes through the same check** (`JobPhotoForm`). `JobPhotoInline.max_num` repeats the count.
- **Storage.** A `FileField`, not an `ImageField`, since the form already decodes. Files are named with a uuid under `job_photos/<year>/<month>/`, by upload month.
- **Never public.** `MEDIA_URL` routes to `protected_media` (`role_required(MANAGER, ADMIN)`). Neither whitenoise nor Caddy serves `MEDIA_ROOT`.
- **Two inputs.** `photo` (`accept="image/*"`, `multiple`) opens the gallery. `camera_photo` (`capture="environment"`) opens the camera; Chrome and Opera on Android offer no camera from a bare `accept` input, and `capture` removes the gallery. `clean()` merges the two into one list.
- **`enctype="multipart/form-data"` is on each caller's `<form>`.** Without it the browser posts only the file name.
- **File inputs lose their picks on every re-render** (an error, or a no-JS row button). That is why the field renders last, with a hint. Stored photos survive, as `remove_photos` checkboxes.
- **Deleting files.** A `post_delete` receiver on `JobPhoto` deletes the file via `transaction.on_commit`. That covers every way a row goes. The one case it can't see, an admin inline swapping the file on a surviving row, is handled in `WorkOrderAdmin.save_formset`. Tests need `captureOnCommitCallbacks`.

`JobPhotoTests` covers all of this.

## Recording a refuelling

`refuel_create` (Tankování, `workorders/refuels.py`) takes optional notes („Poznámky"), the date („Datum provedení", the same `EntryForm` fields and rules as the job's) and one section, *Tankované stroje* (`REFUEL_SECTIONS`). Each row is stroj, zdroj, litry: a machine and litres, or blank; the source is optional (`RefuelRowForm.check_row`). At least one row is needed. It is written in one `transaction.atomic()`.

- **„Zdroj" is a catalog**, `machines.FuelSource` (name, `is_active`), managed in the admin exactly like `Location`: retired with `is_active=False`, protected from deletion. It has no balance; nothing is summed or refused per source.
- **One row per machine** (`RefuelRowFormSet`). One source may feed several machines. A machine filled from two sources is two refuellings.
- **`LITRES_MAX`** caps the litres; the minimum is 0,01, since a fill-up of nothing is not a fill-up.
- **The form machinery is the job form's.** `_entry_forms` builds both pages, so the row buttons, the unbound rebuild, the error banner and the row scripts behave the same. There are no photos and no „Zapsat za".

`RefuellingFormTests` covers this.

**Migration.** Fill-ups used to be rows of a job. Migration `0010_move_fill_ups` gave every job's fill-ups a refuelling with the job's date, author and review state. A fuel-only job (fill-ups and nothing else) was then empty, so it was deleted, with its description and notes carried into the refuelling's notes. The exception is a fuel-only job with photos, which was kept so the photos survive. A job that also recorded work kept everything else, and its refuelling's notes say „Převedeno ze zpracování #N". Migrated fill-ups have no source.

## Review

`WorkOrder.status` and `Refuelling.status` are both `ReviewStatus`: `PENDING` or `APPROVED`, with `reviewed_at` and `reviewed_by`.

**An entry counts only once approved.** Hodiny, Stroje and Materiál report approved jobs and refuellings only. A worker's submission is saved `PENDING`; a manager's or admin's own is saved `APPROVED` with themselves as reviewer, since there's nobody above them (`_review_on_submit`).

The review pages under `/jobs/` and `/refuels/` are all `role_required(MANAGER, ADMIN)` and offer three actions: **approve, edit, delete** (`_approve` is shared). An entry is never handed back to its author, and editing does not approve. Přehled links to the refuelling list („Přehled tankování") with its pending count, and Stroje's „Detail tankování" links each fill-up to its refuelling.

### What a worker sees

A pending job is invisible in every report, so `my_jobs` is the only feedback. It sits at the top of Hodiny and shows the worker's last `MY_JOBS_LIMIT` (5) submissions with a status badge and their own hours.

- It is scoped by `created_by` only. Being named on someone else's job is not recording one.
- It ignores the filter form, which scopes the approved-hours report.
- It is empty for managers, who have Přehled.

Refuellings get the same thing on their own page: `my_refuellings` (`_my_recent_refuellings`) lists a worker's last `MY_JOBS_LIMIT` refuellings under the Tankování form.

### Recording for someone else

Managers and admins get an `author` field („Zapsat za", default „Za sebe"). A worker's form doesn't have it at all.

- With an author set, the job, its hours and its line items belong to the author, and „Moje hodiny" is relabelled „Odpracované hodiny".
- **Approval follows the submitter**, so the job is `APPROVED` with `reviewed_by` = the manager.
- `_submitted_author` reads the author from the POST before any formset is built, because the author decides who may be a collaborator.

`collaborator_queryset(user, viewer)` takes two people:

| Argument | Meaning |
|---|---|
| `user` | the author, who is excluded from the list |
| `viewer` | whoever fills the form in; a plain worker may name only workers |

### Editing

`job_edit` reuses the submission's helpers (`_job_forms`, `_valid_job_rows`, `_write_job_rows`), so a correction is mass-balanced like the original.

- Rows are written as the **author**.
- The collaborator list is built from (author, editing manager).
- The hours label reads „Hodiny – <author>".

**Retired records.** Row pickers show active catalog entries only. On `job_edit` that would be a data-loss bug: a picker missing the stored value renders the row blank, and saving drops it. So `job_edit` collects the pks the job already names, per section, and passes them as `keep`; `_offer_recorded` adds them back. `keep_location` does the same for the location. Only what this job names comes back. `RetiredCatalogOnEditTests` covers this.

`refuel_edit` does the same for its machines (`keep`) and sources (`keep_source`). `RefuellingReviewTests` covers it.

## The job form

`templates/workorders/_job_form_fields.html` is the whole form body: the job's fields plus the four sections from `JOB_SECTIONS` (workers, consumed, produced, machines). Both `transform_form.html` and `job_edit.html` include it, so a new field lands on both pages. The section loop itself is `_row_sections.html`, which the refuelling form's `_refuel_form_fields.html` includes too.

The callers keep only the `<form>` tag (with `enctype`), the CSRF token, the heading, the submit button and the cancel link. „Zapsat za" renders only when the form has it.

### Errors must always render

Nothing is written unless all five forms validate. An error with nowhere to render looks like a button that did nothing.

- Row errors go through `_row_errors.html`, which labels each field error because the row has no visible labels.
- Sections render `non_form_errors`.
- The job form renders every field's errors.
- `RowForm.clean()` returns early if a field already failed, so „Vyplňte materiál i množství…" never covers the real error.
- One red banner tops any refused submission (`_form_error_summary`). It reads „Vyplňte prosím všechna povinná pole." if any error is `code='required'`, otherwise a generic line. There is no banner for a balance error alone or for a row button.

`ErrorBannerTests` covers this.

### Resizing a section (server)

Each section has „+ další řádek" (`add_<prefix>`) and „− odebrat řádek" (`remove_<prefix>`), both ordinary submit buttons. `_entry_forms` (behind `_job_forms` and `_refuel_forms`) checks `_pressed_row_button` before treating the POST as a submission. It then rebuilds every form **unbound** from the raw POST, with that section one row bigger or smaller. Unbound means no "required" errors and no balance check on a form still being filled in.

- **Remove takes the last row**, the one add appends.
- **Bounds.** Sections stay between `MIN_ROWS_PER_SECTION` (1) and `MAX_ROWS_PER_SECTION`. `TOTAL_FORMS` comes straight from the POST, so it is parsed defensively and clamped.
- **The remove button** is rendered with `hidden` at the floor, so the script can unhide it. That needs `button[hidden] { display: none }` in `app.css`.
- **Both buttons carry `formnovalidate`**, or the browser blocks the submit on required fields.
- **`job_row_formset` builds a class per call**, because `extra` is a class attribute.
- **Each `<form>` starts with an off-screen decoy submit** (no `name`), so Enter saves instead of adding a row.

`RowButtonTests` covers this.

### Summing the outputs into the input (server)

A screening job takes one input material and splits it into several outputs, and only the outputs get weighed. So Spotřebováno has a third button, „= součet výstupů" (`sum_consumed`), which fills its row's quantity with the total of the Vyrobeno quantities. The job still has to balance on save; the button just saves the arithmetic.

- **Declared on the section.** `JobSection.sum_from` names the source section and `sum_field` the field summed and filled. Only Spotřebováno sets them, so the refuelling form has no such button.
- **Same path as the row buttons.** It is a `formnovalidate` submit. `_entry_forms` checks `_pressed_sum_button` and rebuilds every form unbound from the POST, with `_summed_rows` filling the section's row. Nothing is saved and nothing is validated.
- **Only for a single row.** With more consumed rows there is nothing sensible to fill, so the server leaves them alone and the template renders the button `hidden`.
- **Lenient input.** Blank or unparsable outputs are skipped, since the real submission reports them. If nothing is typed, the row keeps what it had. A typed consumed amount is replaced. The total is written with `format(..., 'f')`, so `<input type="number">` never gets an exponent.

`SumButtonTests` covers this.

### Tonnage follows the consumed total

The machines usually process exactly what the job consumed, so each Použité stroje row's „Tuny" is filled with the Spotřebováno total by default. It is a default, not a derivation: the user can type over it, and `MachineUsage.tons` stays its own number on save.

- **Declared on the section.** `JobSection.mirror_from` names the source section, `mirror_source` the field summed there and `mirror_field` the field filled. Only Použité stroje sets them.
- **Only rows naming a machine.** A tonnage with no machine is refused (`check_row`), so a spare row is never filled.
- **Server: on every rebuild.** Any row or sum button rebuilds the forms unbound, and `_mirrored_rows` fills each blank tonnage from the rebuilt consumed rows (after the sum, so a summed row counts). A typed value is kept. A real submission is never filled in: a blank box stays unknown.
- **Script: live.** `job_rows.js` keeps a box in step while it is blank or still holds the last total it copied in; anything else counts as typed. Picking a machine fills its blank box, and clearing the machine empties a box that held only the total.

`TonnageMirrorTests` covers the server path.

### The three scripts

`job_rows.js` and `searchable_select.js` come with `_row_sections.html`, so they also run on the refuelling form; `photo_picker.js` is the job form's alone. All three are **progressive enhancement**. The test suite has no browser, so the server path must be complete by itself, and with any script blocked the form still works. Each script builds no markup of its own and reads everything off the server-rendered page. The tests cover the contract they read, not their behaviour.

**`static/js/job_rows.js`** resizes sections in the DOM. A new row is the section's `<template>` (Django's `formset.empty_form`) with `__prefix__` replaced, plus a `TOTAL_FORMS` increment.

- `empty_form` gets the formset's `form_kwargs` (collaborator scope, `keep`) and is not narrowed by `_hide_taken_choices`; the script hides taken options live.
- It keeps four agreements with the server:
  - remove takes the last row;
  - the bounds are read from `data-min-rows`/`data-max-rows`;
  - each row and its errors share a `.row-block`;
  - duplicates are only hidden or disabled, and only in the picker named by the section's `data-unique-field` (a refuel row's „Zdroj" is left alone).
- It also handles „= součet výstupů": it sums `data-sum-field` across the `data-sum-from` section, in hundredths so float error never shows, and hides the button unless the section has exactly one row.
- It keeps machine tonnage in step with the consumed total: `data-mirror-field` on each row naming a machine follows the `data-mirror-source` total of the `data-mirror-from` section (see above).

Tests: `RowTemplateTests`.

**`static/js/searchable_select.js`** puts a type-to-filter box in front of every `<select>` in a row: one per row on the job form, two (stroj and zdroj) on the refuelling form. Each one's `empty_label` is a bare noun, which becomes the placeholder.

- The `<select>` keeps its name and value and is still what gets posted (`hidden` is not `disabled`).
- The box has an `id` but no `name`, and a 40-character client-side cap (`SEARCH_MAX_LENGTH`).
- Options are re-read every time the list opens, so it agrees with `_hide_taken_choices` and `job_rows.js` for free. A pick dispatches `change`.
- Matching is case- and diacritic-insensitive („ster" finds „Štěrk").
- New rows are found with a `MutationObserver`, so neither script depends on the other.
- Enter submits when the list is closed and picks when it is open.

Tests: `SearchablePickerTests`, including the three `.combo` CSS rules.

**`static/js/photo_picker.js`** hides the two photo inputs behind one „Přidat fotku" button whose menu calls `click()` on one or the other.

- A file input holds one pick. So after each pick the filled input moves, hidden and without its `id`, into a list entry, and an empty clone takes its place.
- Every input keeps its `name`, so every pick is posted; „Odebrat" removes an entry with its input.
- The button, menu and entry come from `<template data-photo-picker>` / `<template data-photo-entry>`, and every button in them is `type="button"`.
- It doesn't touch stored photos and doesn't count against `PHOTO_MAX_COUNT`.
- `app.css` needs `.photo-field [hidden] { display: none }`.

Tests: `PhotoPickerTests`.

## Installing the app (VyrobaPK)

The site installs on a phone's home screen as **VyrobaPK**: Chrome's „Nainstalovat aplikaci", or Safari's Share → „Přidat na plochu". It is the same site, opened full-screen.

`workorders/pwa.py` serves three public URLs:

| URL | What |
|---|---|
| `/manifest.webmanifest` | A template, so the icon URLs go through `{% static %}` and are hashed in production |
| `/sw.js` | The service worker, also a template. It must sit at the root to control the whole site |
| `/offline/` | The page shown when a page load fails |

`base.html` links the manifest and loads `static/js/pwa.js`, which registers the worker from its own `data-service-worker` attribute.

**The worker does one thing:** it answers a failed navigation with the cached offline page, with network-first fallbacks for that page's CSS and logo. It caches no other page, ignores non-GET requests and queues nothing. Offline submission would be script-only behaviour, and a project of its own.

- **The offline page is cached anonymously** (`credentials: 'omit'`): no nav, no user, no CSRF token.
- **It updates itself.** `service_worker` embeds a hash of the cached URLs and of the offline page's markup, so changing either ships a new worker.

The icons are the stone mark from `logo.png` on white, at 192 and 512 px, plus a maskable 512 px version with the mark inside the 80 % safe zone. The manifest's `theme_color` matches the light `theme-color` meta, kept in step by hand.

Service workers need HTTPS or `localhost`. To test on Android against the dev server: `adb reverse tcp:8000 tcp:8000`, then open `http://localhost:8000`. The Claude desktop browser pane refuses service workers. `PwaTests` covers the contract.

## Reports

Hodiny, Stroje and Materiál share one shape: a filter, a summary table, and the detail rows the summary is made of. One dataset at two zoom levels, under one filter. Every summary is built from the filtered rows (`<rel>__in=rows.values('pk')`), so the two tables can't disagree.

### Stroje

`machine_dashboard` (manager only) has two summary/detail pairs: usage and fuel.

**Every active machine is listed**, even at zero, so a machine at `0 h` answers "what ran last week". Naming a machine in the filter narrows the table to it, and an invalid filter shows nothing.

**Motohodiny are meter readings**, so they are never summed:

- **„Stav motohodin"** is `Max` of the filtered readings: where the meter stood at the end of the range.
- **„Hodin na zakázce"** is each row's `run_hours`, from `_with_run_hours`: its reading minus that machine's previous approved reading.
- **„Hodin za období"** sums the known `run_hours`, and that is what „Cena za hodiny" prices.

Three rules apply to the calculation (`MachineReadingTests`):

- "Previous" means the next lower reading, tie-broken on pk, not the previous date. The meter only counts up, and jobs arrive late or back-dated. Nothing is stored, so every render recomputes.
- The baseline is every approved row of that machine, not just the filtered ones.
- A machine's first reading, or a blank one, has no run time (`None`).

**Money.** `hourly_rate` (Kč/hod) and `rate_per_ton` (Kč/t) price three columns, computed per row by `_machine_costs`. That is why `_machine_summary` returns a list.

| Column | Value |
|---|---|
| Cena za hodiny | hours run × `hourly_rate` |
| Cena za tuny | tonnage × `rate_per_ton` |
| Celkem | the sum of the sides the machine is priced on |

The distinction throughout is unknown versus zero:

- **An unset rate means "not priced that way", not free.** That side shows „—" and stays out of „Celkem".
- **A priced machine with no rows costs `0,00`.** A machine with rows but no known run time has an unknown cost. `filtered_usages` tells the two cases apart.
- **„Celkem" is unknown if any priced side is.** Half a bill is worse than none.
- `Sum` skips NULLs, so all-unknown tonnage is „—", never `0 t`.

**Fuel.** The *Tankování* card (`_refuel_summary`) shows litres and fill-up count per machine from approved refuellings, under the same filter and with the same two listing rules. `RefuelFilterForm` is `MachineFilterForm` with its date and author read off the refuelling. Its zeros are real (`Coalesce`), so it stays a queryset. „Detail tankování" shows each fill-up's source and links to its refuelling.

The two detail tables paginate independently. `_paginated(request, rows, param)` gives each its own page parameter and keeps the other table's page in its links. `MachineRefuelReportTests` covers this.

### Materiál

`material_dashboard` (manager only) is the only page that reads line items across jobs: "how much 8/16 did we make last month?"

| Column | Annotation |
|---|---|
| Spotřebováno | `Abs(Sum(...))` of consumed rows |
| Vyrobeno | `Sum(...)` of produced rows |
| Rozdíl | `Coalesce(Sum(...), 0)` of all rows; signed storage makes this the net |

Zeros are real here (`0,00 t`). Every active material is listed, and naming one narrows the table. `MaterialFilterForm` offers retired materials and locations, so their history stays reachable. It has `location` but no `created_by`.

### Hodiny

`time_worked` (any logged-in user) sums `WorkerHours.hours` per person. It re-queries the scoped jobs by `pk__in` before aggregating, because the collaborator join would otherwise multiply the `Sum`.

A worker sees only their own row, scoped twice: the queryset through `participation_filter`, and then the summary rows filtered to `request.user`. That second pass stops a collaborated job from showing the creator's hours. Hodiny has no fixed relationship to the motohodiny on Stroje.

### CSV exports

| Export | URL name | Built from |
|---|---|---|
| Hodiny „Souhrn" | `time_worked_export` | `_time_worked_scope` |
| Stroje „Stav strojů" | `machine_dashboard_export` | `_filtered_machine_usages` + `_machine_summary` |
| Stroje „Tankování" | `machine_refuel_export` | `_filtered_machine_refuels` + `_refuel_summary` |
| Stroje „Detail tankování" | `machine_refuel_detail_export` | `_filtered_machine_refuels` |
| Stroje „Detail používání strojů" | `machine_usage_detail_export` | `_filtered_machine_usages` |
| Materiál „Souhrn materiálů" | `material_dashboard_export` | `_filtered_material_movements` + `_material_summary` |
| Materiál „Detail položek" | `material_detail_export` | `_filtered_material_movements` |

How the exports behave:

- **Same helpers as the page, same querystring.** The file is the table that was on screen: pending jobs excluded, and an invalid filter gives a header-only file. `_time_worked_scope` was extracted so that a worker's export is scoped exactly like their page.
- **Same gate as the page.** `time_worked_export` is `login_required`; the rest are manager only.
- **Detail exports hold every filtered row, not the current page.** Line items and fill-ups get reconciled against delivery notes and fuel receipts; usage rows carry the run time per job. A usage row's „Hodin na zakázce" is `_with_run_hours` over the unfiltered baseline, exactly as on the page.
- **Line items carry their job's ID.** „Detail položek" starts with an „ID" column (`work_order_id`, a `#n` link to `job_detail` on the page, the bare number in the file), since two jobs can share a date, description and author.
- **Format.** `_csv_response` writes `;`-delimited rows behind a UTF-8 BOM, because Excel under `cs` splits on `;` and needs the BOM for „Š". Numbers go through `number_format` (`12,50`). Unknowns are blank cells (`_csv_number`), never `—`, which would break a `SUM`. Dates are ISO. No openpyxl.

`TableExportTests` covers this.

## Pages and URLs

All URLs are in `workorders/urls.py`, mounted at the root. Login and password change are Django's generic views in `config/urls.py`.

| URL | Name | What |
|---|---|---|
| `/` | `transform_create` | Zpracování: the job form and landing page |
| `/hours/` | `time_worked` | Hodiny |
| `/machines/` | `machine_dashboard` | Stroje |
| `/materials/` | `material_dashboard` | Materiál |
| `/jobs/` | `job_dashboard` | Přehled: every job, pending ones highlighted |
| `/jobs/<pk>/` | `job_detail` | One job, with review actions |
| `/jobs/<pk>/fotky/<n>/` | `job_photo` | „Fotka n" of the job, with back and previous/next buttons (the phone viewer) |
| `/jobs/<pk>/upravit/` | `job_edit` | Correct a job |
| `/jobs/<pk>/schvalit/` | `job_approve` | POST only |
| `/jobs/<pk>/smazat/` | `job_delete` | GET confirms, POST deletes |
| `/refuel/` | `refuel_create` | Tankování: the refuelling form |
| `/refuels/` | `refuel_dashboard` | Přehled tankování: every refuelling, pending ones highlighted |
| `/refuels/<pk>/` | `refuel_detail` | One refuelling, with review actions |
| `/refuels/<pk>/upravit/` | `refuel_edit` | Correct a refuelling |
| `/refuels/<pk>/schvalit/` | `refuel_approve` | POST only |
| `/refuels/<pk>/smazat/` | `refuel_delete` | GET confirms, POST deletes |
| `/hours/export/` | `time_worked_export` | CSV |
| `/machines/export/` | `machine_dashboard_export` | CSV |
| `/machines/detail/export/` | `machine_usage_detail_export` | CSV |
| `/machines/fuel/export/` | `machine_refuel_export` | CSV |
| `/machines/fuel/detail/export/` | `machine_refuel_detail_export` | CSV |
| `/materials/export/` | `material_dashboard_export` | CSV |
| `/materials/detail/export/` | `material_detail_export` | CSV |
| `/media/<path>` | `protected_media` | Job photos, manager only |
| `/manifest.webmanifest`, `/sw.js`, `/offline/` | `web_manifest`, `service_worker`, `offline` | PWA, public |

`views.py` records and reviews jobs and holds the entry-form helpers. `refuels.py` records and reviews refuellings on top of them. `reports.py` holds the reports, exports and list helpers. `REVIEWER_ROLES` lives in `accounts/decorators.py`, so neither module imports the other for it.

The reports stay a module rather than a separate app: they aggregate only `workorders` models, and a separate app would need shared helpers moved out to avoid a circular import, for no behaviour change.

The bottom nav is hardcoded in `base.html`. Everyone gets Zpracování, Tankování and Hodiny; managers also get Přehled, Stroje and Materiál. `<body class="page-{{ url_name }}">` enables page-scoped CSS, so renaming a URL name drops that page's styles.

### Desktop layout

Phones and desktops get the same HTML. The phone layout is the default; one `@media (min-width: 900px)` block at the end of `app.css` rearranges it for wide screens, so no device is detected and a resized window or rotated tablet just reflows:

- The bottom nav is the same on every width; it was tried as a top bar on desktop and deliberately kept at the bottom.
- `main` widens from 480px to 640px, which is enough for the job form and `job_detail`. A page that holds tables opts into 1200px with `{% block main_class %}wide{% endblock %}`: today Přehled, Přehled tankování, Hodiny, Stroje and Materiál. A class set by the template, rather than `body.page-<url_name>`, so renaming a URL can't drop it.
- The filter card's form (`.filter-form`) lays its fields side by side, with any errorlist on a row of its own.
- `job_detail` and `job_edit` with photos pin the photo panel at the right (see [Photos](#photos)), and `main.with-photo-panel` sits beside it.

Separate desktop templates chosen by user agent were considered and rejected: the user agent is unreliable (iPads report as Macs), and every page and error path would need rendering and testing twice.

## Roles and permissions

| Role | `is_staff` / `is_superuser` | Access |
|---|---|---|
| `WORKER` | no | Zpracování and Hodiny; own records only; submissions await approval |
| `MANAGER` | no | adds Přehled, Stroje, Materiál and everyone's records |
| `ADMIN` | yes / yes | adds the Django admin |

The gates:

- **`role_required(*roles)`** on views. Anonymous users get the login redirect; the wrong role gets a 403. The `/jobs/` pages, Stroje, Materiál and `protected_media` all use it. Hiding a nav link is not access control.
- **`User.is_manager_or_admin`** for the nav and query scoping.
- **`AdminSessionRequiredMiddleware`** on `/admin/` (below).

Further rules:

- **Superusers bypass all three**, because `createsuperuser` sets no role.
- **Django permissions are unused.** `auth.Group` is unregistered and the user admin hides groups and permissions. Every admin user is a superuser anyway.
- **`ADMIN` accounts get both `is_staff` and `is_superuser`.** `is_staff` alone shows an empty admin. `seed_data` and `CustomUserAdmin.save_model` derive both flags from `role`.

### The admin is a 404 unless you are already an admin

The server is public, and Django's admin login form would otherwise be a superuser password form at the URL every scanner tries. `accounts/middleware.py::AdminSessionRequiredMiddleware` answers **404** for everything under the admin prefix, `/admin/login/` included, unless `user.has_admin_access`:

```python
self.is_active and self.is_staff and (self.is_superuser or self.role == self.Role.ADMIN)
```

The only login form left is the app's own `/login/`, which axes rate-limits. Admins log in to the app first, then open `/admin/`.

- **404, not a redirect.** A redirect would confirm the admin exists.
- **Middleware order matters.** It sits **above `CommonMiddleware`**, or `APPEND_SLASH` turns `/admin` into a revealing 301. It sits above `CsrfViewMiddleware` too, so an anonymous POST is a 404 rather than a 403. Being above `AuthenticationMiddleware`, it resolves the user itself with `get_user`.
- **The prefix comes from `reverse('admin:index')`**, read lazily, and the user is looked up only for admin paths.
- **The header link uses the same property.**
- **It hides the login form, not the admin's existence.** `/static/admin/` still shows there is one. The `Caddyfile`'s commented IP block or a VPN are optional extra layers.

Tests: `AdminGateTests`, `AdminLinkTests` and the smoke script, which also requests `/admin` without the slash.

## Filter forms

Every filter form subclasses `DateRangeFilterForm`. It holds `date_from`/`date_to`, the „od ≤ do" check, and `filter(queryset)`:

| Request | Result |
|---|---|
| no querystring (`is_bound` false) | everything in scope |
| valid filter | filtered |
| **invalid** filter | **`.none()`**, plus a warning |

Returning everything for a bad filter, under a "filtered results" heading, would look like an answer. A subclass declares `lookups` (field name to ORM lookup, or to a callable returning a `Q`) and `date_lookup` (`performed_on`, or `work_order__performed_on` for row-level pages), plus a `field_order` so the dates render last.

**Worker scoping** is done in the queryset, never by hiding fields. Only `time_worked` needs it, being the only filtered page a worker can open. `TimeWorkedFilterForm` deleting its `worker` field for a worker is cosmetic; the queryset filter is what makes `?worker=<someone>` a no-op (`test_worker_cannot_bypass_restriction_via_worker_param`). Don't add scoping to `role_required` views.

**Quick date ranges.** Vše, posledních 7 dní, posledních 30 dní and minulý měsíc come from `DATE_PRESETS` / `_date_preset_links`. They are plain links that set `date_from`/`date_to`, keep the other parameters and drop the page parameters. The spans include today, and the active one is highlighted.

A new filtered page needs both:

- `**_list_page_context(request, rows)`: `page_obj`, `querystring` and `date_presets`. Taking only part of it makes the filter vanish on page two.
- The `_filter_card.html` and `_pagination.html` includes.

## Deliberately absent

- **Stock balances.**
- **A REST API, htmx, widget_tweaks.** All were installed, never used, and removed.
- **A JS framework or CDN scripts.** Three enhancement scripts on the job form, plus `pwa.js`.
- **A CSS framework or per-template CSS.** One `app.css`. Every colour is a custom property declared for light and dark; dark mode follows the OS, with no toggle.
- **Soft deletes.** Catalog entries are retired with `is_active=False` and protected by `PROTECT`.
