# Architecture

A modular monolith: one Django project (`config`) with five code-bearing apps
that depend on each other in one direction only — `accounts`, `materials`,
`machines` and `locations` hold the reference data, and `workorders` owns the
jobs, their material line items, the machine usage rows, and every page.
`materials` and `machines` do not know about each other; the one
cross-reference is `seed_data`, which lives in `materials` and loads all three
CSVs. `locations` has no CSV: its catalog is maintained in the admin.

```
accounts ───┐
materials ──┤
machines ───┼──> workorders
locations ──┘
```

> **This app does not track stock.** Receipts, shipments, adjustments, the stock
> dashboard, the movement history and its CSV export were all removed, along with
> `Material.track_stock` and every balance and sufficiency check. Nothing sums
> quantities into an on-hand figure. The repository name is historical.

---

## Job line items

`workorders.StockMovement` keeps its historical name, but it is no longer a
ledger. One row is **one material line on one job**: what the job consumed or
what it produced.

| Field | Meaning |
|---|---|
| `material` | What was processed. |
| `quantity` | **Signed.** Negative for consumed, positive for produced. Always tonnes — a material has no unit field, so the `t` shown in the form prompt and the job detail is hardcoded. |
| `movement_type` | `TRANSFORM_CONSUME` or `TRANSFORM_PRODUCE`. Nothing else. |
| `work_order` | The job this line belongs to. **Required.** |
| `created_by` | Who recorded it. |

**A line item has no timestamp of its own.** Its date is its job's
`performed_on`, which is what Materiál filters and sorts on, and
`Meta.ordering = ['id']` puts the rows of a job in the order they were typed.

### Not in the admin

`StockMovement` is deliberately **not registered** with the Django admin. A line
item never exists apart from the job it belongs to, and `WorkOrderAdmin` already
edits the rows as an inline, so registering it as well only added a second
top-level section to the admin index — labelled *Zpracování*, the same word as
the worker-facing form, and holding one model already reachable from *Zakázky*.
Everything about a job is edited in one place.

The `date_hierarchy` that used to live on the movement list moved to
`WorkOrderAdmin`, which is now the only admin that has one — and the only place
the Czech date-hierarchy string from `locale/cs` is exercised.

### What this means for new code

There is no available-quantity helper, no `select_for_update()`, and no check to
perform before writing a line item. A job records what a worker says happened.
If you ever need balances back, you are adding a genuinely new subsystem — read
the git history rather than assuming any of the old machinery is still wired up.

## Work orders

A transformation is one `WorkOrder` plus everything it produced:

```
WorkOrder #17  "Crushing gravel"
├── StockMovement  TRANSFORM_CONSUME  −20 t  Raw stone   @ Yard
├── StockMovement  TRANSFORM_PRODUCE  +14 t  Gravel 8/16 @ Yard
├── StockMovement  TRANSFORM_PRODUCE  +6 t   Gravel 4/8  @ Yard
├── MachineUsage   Crusher  3.5 h, 20 t
├── MachineRefuel  Crusher  40 l
├── WorkerHours    novak 6 h,  svoboda 4 h
└── collaborators  [svoboda]
```

The consumed and produced sides of that job add up to the same 20 t, and that
is enforced. **A submission must carry at least one consumed row and one
produced row, and their totals must be exactly equal**; otherwise
`transform_create` re-renders the form with an error and writes nothing at all —
not the line items, not the hours, not the machine usage.

**One kind of submission is exempt, and only one**: a job that is nothing but
fill-ups. See [A job that is only fuel](#a-job-that-is-only-fuel).

The comparison is between *totals*, not between matching rows, because the
normal case is one input crushed into several output fractions. Equality is
exact `Decimal` comparison with no tolerance: `5.00` balances against `5`,
while `5.01` against `5` is refused. It lives in the view, before the
transaction opens, because unlike the old stock check it needs no database
state — only the rows being submitted.

> The two sides are summed together, so they only mean something in a shared
> unit. The real catalog is entirely in tonnes (`Zdroj` and `Frakce` alike), but
> nothing in the model enforces that; a mixed-unit catalog would make the rule
> compare quantities that are not comparable.

The whole submission is one `transaction.atomic()` block. Nothing inside it can
fail a business rule — the balance check has already run by then — but the
transaction stays: the line items, the hours and the machine usage are one job
and must not land half-written.

Formset rows are written **exactly as typed** — nothing is combined on the way
in, because nothing needs to be: no section may name the same thing twice.

### One row each

**A material appears at most once per section, a machine at most once, a person
at most once.** Two rows naming the same material are two halves of a quantity
that should have been typed once, and a reader downstream cannot tell them apart
from a job that really did handle it in two passes; two rows for one machine
double it in the Stroje totals; two rows for one person used to be quietly added
together, because the `UniqueConstraint` on `(work_order, user)` would otherwise
have failed the write with a 500.

The rule is **per section**, so one material may still be consumed *and*
produced by a single job — those are two different statements about it, and the
mass balance compares the two sides precisely because a material can cross them.

`UniqueChoiceFormSet` in `workorders/forms.py` carries both halves, and each
section subclasses it with the field it applies to and the Czech complaint:
`MaterialRowFormSet`, `MachineRowFormSet`, `WorkerRowFormSet`.

- **The rule** is `clean()`, on the POST. It puts the error on the *repeated*
  row rather than as a non-form error, because the message names one row of
  several and belongs where the reader can see which. Like the mass balance, it
  refuses the whole job: nothing at all is written.
- **The help** is `_hide_taken_choices()`, which drops what the other rows
  already took out of a row's dropdown — so the duplicate is not offered in the
  first place. It runs on **unbound** formsets only: a bound one is being
  validated, and narrowing a queryset there would turn a duplicate into
  „Vyberte platnou možnost." on whichever row lost the race instead of the
  message above. Every row keeps its own choice, or it would have no option to
  re-select and would render blank.

Server-side the help can only be as fresh as the last render: the list a row is
showing was built when the page was. The renders that matter are the ones where
a row is about to be filled in — „+ další řádek", the tap that asks for an
empty row, and the edit form's spare row. `job_rows.js` narrows the same lists
live as the dropdowns change, which is fresher than any render can be, but it
is an enhancement and may not be running. Choosing the same thing twice between
two renders is exactly what the rule is for. `DuplicateRowTests` in
`workorders/tests.py` covers both halves.

One asymmetry falls out of the two working together. A row the server rendered
had the taken options `exclude()`d from its queryset, so they are not in the
DOM at all and freeing one up again cannot bring them back — the script can
only hide what is there. A row the script added carries the whole catalog and
does behave that way. This is no worse than having no script; the alternative,
dropping the server-side narrowing so the script owns it, would cost the
feature entirely to anyone whose browser never ran it.

### What a job says it was

Two text fields, and they are deliberately not the same shape.

`WorkOrder.description` (*„Popis"*) is a **required** one-liner — a
`CharField(max_length=255)` rendered as a single-line `<input>`. It is the
job's label: it is the „Zakázka" column on Přehled, on Hodiny, on Stroje and on
Materiál, and it is what the Materiál detail export carries per row, so a job
without one reads as a blank cell in four places. Requiring it costs the person
filling the form in one short sentence and is why those columns say something.

`WorkOrder.notes` (*„Poznámky"*) is the **optional** long one — a `TextField`
rendered as a four-row `<textarea>`, for whatever did not fit on the line:
a breakdown, a change of plan, who to ask about it. It appears only on the job
detail page, rendered through `linebreaksbr` so the typed line breaks survive;
no list column, no filter and no export reads it, because it is prose and none
of those are prose columns.

The form caps it at `NOTES_MAX_LENGTH` (2000) while the column has no limit of
its own. That is the allowed direction — a form tighter than its model needs no
migration — and it turns an accidental paste into a field error rather than an
unbounded row. The opposite direction is the bug the `hours` fields guard
against; see [Resizing a section](#resizing-a-section) for `HOURS_MAX_DIGITS`.

Both live on `_job_form_fields.html`, so `transform_create` and `job_edit` get
them together, and both render their own errors — the required one especially,
since a refused submission with no message on the page looks exactly like a
button that did nothing.

Older rows predate the requirement and may still hold an empty description;
nothing backfills them, and the pages keep printing „—" for one. So does a
fuel-only job, which is not asked for either field — see
[A job that is only fuel](#a-job-that-is-only-fuel).

Both fields carry `required=False` on `WorkOrderForm` and are re-imposed by
`_require_work_fields` once the rows are known, because the form alone cannot
tell a job that records work from one that records a tank of diesel. The error
is Django's own `required` message, taken off the field, so it reads the same as
every other required field on the page and comes from the same catalog.

### Where a job happened

`WorkOrder.location` (*„Lokace"*) is a foreign key to `locations.Location`, a
catalog of name + `is_active` edited in the admin under *Lokace*. Machine
operators work at several sites, some of which keep their material records
separately and some of which keep none at all; the field says which one a job
belongs to. **It is a label on the job, not a stock location** — one per job,
not per line item, and nothing is balanced, summed or refused per location. The
mass balance is the same everywhere.

- **Required on every job that records work, exempt on a fuel-only one** — it
  is the third entry in `WORK_FIELDS`, beside „Popis" and „Moje hodiny", and
  carries `required=False` on the form for the same reason they do.
- **Nullable in the model**, because jobs recorded before the column existed
  have none and a fuel-only job need not have one. The pages print „—" for it
  and the Materiál detail export a blank cell.
- **Retired like a machine or a material.** `on_delete=PROTECT` blocks deleting
  a location any job names; unticking `is_active` takes it out of the form's
  picker. `job_edit` passes the job's own location as `keep_location`, which
  goes through the same `_offer_recorded` as the row pickers, so a job at a
  retired location still renders with it selected — see
  [Editing a job that names a retired record](#editing-a-job-that-names-a-retired-record).
- **Shown and filtered on Přehled and Materiál only** — a column and a
  „Všechny lokace" filter on each (`JobFilterForm.location`,
  `MaterialFilterForm.location` via `work_order__location`), a row on the job
  detail page, and a column in the Materiál detail export. Materiál's summary
  follows the filter because it aggregates the same filtered rows. Hodiny and
  Stroje do not read it.
- It is a plain `<select>`, not one of the searchable row pickers:
  `searchable_select.js` only enhances the four sections.

### When a job happened

`WorkOrder.performed_on` is the day the work was done; `created_at` is when
somebody typed it in. They differ only when the submitter edits the date box
on the Transform form, which otherwise already holds today.

The field is **required and pre-filled with today**
(`initial=timezone.localdate`), so the ordinary case needs no thought and
back-dating is just editing the box. An empty date, or one in the future, is a
form error, and the all-or-nothing rule applies: no hours, no machines and no
line items are written.

Day only, no time. An `<input type="date">` renders identically everywhere,
whereas the `datetime-local` widget the removed Příjem/Výdej forms used let an
English-configured phone show AM/PM regardless of the page's `lang="cs"`.

**The widget needs `format='%Y-%m-%d'`.** This is the app's only unbound date
field holding a python `date`, and Django would otherwise render it through the
Czech `DATE_INPUT_FORMATS` as `02.09.2026` — which `<input type="date">` refuses
and displays as an empty box, silently losing the pre-fill. Two tests assert the
rendered `value=`.

`job_edit` can correct the date, and its form shows the job's own date rather
than today, so a correction that touches nothing else does not move it.

**Every report keys off `performed_on`.** The date filters on Hodiny, Stroje,
Materiál and Přehled compare it directly — it is a `DateField`, so a plain
`__gte`/`__lte` with no `__date` lookup and no timezone conversion behind it —
and each list's *Provedeno* column shows it. Ordering is
`['-performed_on', '-created_at']` on all four lists and in `WorkOrder.Meta`:
the day the work happened, then entry order within a day.

Two consequences worth knowing:

- **Machine-usage and line-item rows are dated by their job**
  (`work_order__performed_on`), never by `MachineUsage.created_at`. `job_edit`
  deletes and rewrites every usage row, so a corrected job would otherwise jump
  to the day it was corrected. A `StockMovement` has no timestamp of its own at
  all, so its job's date is the only one it could be shown under.
- **`my_jobs` is the exception**, still ordered `-created_at`. It is a recency
  list of *submissions*: a job someone has just back-dated to last month has to
  appear at the top of it, because checking what became of a fresh submission is
  the only reason that list exists. Its date column still shows `performed_on`.

`created_at` keeps its own meaning — when the job was typed in — and is still
shown on the job detail page (*Zaznamenáno*), on the edit and delete pages, and
in the admin.

### Recording for somebody else

A manager or admin sees a *„Zapsat za"* select on the Transform form (default
*„Za sebe"*); a worker's form does not have the field at all, so an `author` in
their POST is never cleaned and there is nothing for the view to reject.

When it is set, the job is **the other person's**: `created_by=author`, and
`_write_job_rows` writes their hours and their line items, exactly as `job_edit`
does. The field also relabels *„Moje hodiny"* to *„Odpracované hodiny"*, which
would otherwise be wrong half the time.

**Approval follows the submitter, not the author.** A manager typing a worker's
job in is its reviewer and has just seen the work written down, so the job is
saved `APPROVED` with `reviewed_by` = the manager and `created_by` = the worker.
The worker then finds it among their own submissions on Hodiny, already signed
off.

`collaborator_queryset(user, viewer=None)` takes two people for this reason:

| Argument | Role |
|---|---|
| `user` | the job's **author** — excluded from the list, since you cannot collaborate with yourself |
| `viewer` | whoever is **filling the form in** — decides how wide the list is |

They are the same person except when a manager acts for someone else, and then
the manager's privilege wins: the workers-only restriction is there to stop a
*worker* assigning hours to a manager, not to stop a manager recording that a
manager worked the job. `job_edit` passes the same pair. A worker recording
their own job is unaffected — `viewer` defaults to `user`.

Because the author is chosen on the same form that the collaborator rows live
on, `transform_create` reads it out of the POST with `_submitted_author`
**before** any formset is built, on a submission and a row-button rebuild alike.
When „Zapsat za" is empty or invalid, the submitter stands in.

### Approval

A `WorkOrder` carries a `status` — `PENDING` or `APPROVED` — plus `reviewed_at`
and `reviewed_by`.

**A job counts only once it is approved.** Hodiny, Stroje and Materiál all
report `status=APPROVED` rows and nothing else, so a job a worker
submits is recorded immediately but stays out of the reports until a manager
signs it off. A manager's or admin's own submission is written `APPROVED` with
themselves as the reviewer — there is nobody above them to approve it, so
waiting would leave it stuck forever.

There are only two outcomes, and both are the manager's to carry out: approve
the job, or fix it (`job_edit`) — and if it is beyond fixing, delete it
(`job_delete`). A job is never handed back to its author; **only a manager can
correct a job**. There is no `RETURNED` status and no review note.

Because of that, `time_worked` puts the requesting worker's own last
`MY_JOBS_LIMIT` submissions at the top of Hodiny as `my_jobs` (built by
`_my_recent_jobs`), each with its status badge and their own hours on it
(`Sum('worker_hours__hours')` filtered to that user, so a collaborator's hours
stay off it). **This list is the only feedback a worker gets about the review**:
a pending job is filtered out of Hodiny and the machine history, so without it
there is no way to tell a job still waiting for approval from one that never
landed. Hodiny is where it belongs precisely because that is the page the
withheld hours are missing from.

Two scoping rules it does not share with the report underneath it:

- It is scoped by `created_by` alone, **not** by `participation_filter` — being
  named on someone else's job is not recording one, and those hours show up in
  the summary once the job is approved.
- The filter form does not touch it. The filters narrow the approved-hours
  report; a pending job is exactly what that report cannot show, so scoping the
  list with them would hide the thing it exists to surface.

`_my_recent_jobs` returns `none()` for a manager or admin: Hodiny is the whole
depot's report for them, and Přehled already lists every job with its status.

Editing does **not** approve. A manager can fix a job and still leave the
sign-off to someone else, so `job_edit` never touches `status`.

`job_edit` rewrites the job's rows through the same helpers the submission uses
(`_collect_rows`, `_balance_error`, `_write_job_rows` in `workorders/views.py`),
so the mass balance is enforced on a correction exactly as on the original. Two
things it has to get right, and both have a test:

- The rows belong to the **author**, not to the reviewer: the *„Moje hodiny"*
  field is the author's hours, `collaborator_queryset` is built from the author
  and the editing manager (so it excludes the author, not the manager, and the
  manager's privilege decides its width), and rewritten `StockMovement` rows
  keep `created_by = author`.

The two pages share their markup as well as their helpers: the job's own fields
and the four formset sections live in `templates/workorders/_job_form_fields.html`,
included by both `transform_form.html` and `job_edit.html` inside each page's own
`<form>`. **Add a field to a formset row and there is one template to change, not
two.** Only what genuinely differs stays with the caller — the heading, the primary
submit button, and the *Zpět bez uložení* link. Two details make one partial serve both:
*„Zapsat za"* is wrapped in an `{% if order_form.author %}`, which is false on
`job_edit` because it builds `WorkOrderForm` without a `user` and the field is
never added; and the hours label is read off the form rather than written out,
because each page means something different by it — `job_edit` sets it to
*„Hodiny – <author>"*, since a manager correcting somebody else's job is looking
at neither *„Moje hodiny"* nor a bare *„Odpracované hodiny"*.

**The partial renders every error either page can produce, and that is a rule.**
Nothing is written unless all five forms validate, so an error with nowhere to
render is not a cosmetic gap — it is a submission that disappears. The page comes
back carrying what was typed, with no message anywhere, looking exactly as though
the button had not been pressed. Each row's errors go through
`templates/workorders/_row_errors.html` (its non-field errors *and* a labelled
line per field error, since the row's selects have no visible labels of their
own); each section renders its `non_form_errors`; and the job form renders its
`description`, `notes`, `author` and non-field errors alongside the two that
were always there. The row forms also bail out of their own `clean()` when `self.errors` is
non-empty — a field that failed validation is missing from `cleaned_data`, which
reads exactly like a half-filled row, so the "fill in both, or leave it empty"
message would otherwise be printed *over* the real complaint and tell the reader
to do something they had already done.

### Editing a job that names a retired record

A catalog entry retired after a job was recorded is still on that job. The row
pickers are scoped to `is_active=True` — a retired material must not land on a
*new* job — but applying that scoping to `job_edit` is a data-loss bug: a
`ModelChoiceField` whose queryset excludes the stored pk renders the row with
nothing selected, and saving the form as rendered posts an empty row. Since
`_write_job_rows` clears and rewrites, the machine usage is then deleted outright
and the edit still reports success, while a vanished consumed row leaves the job
behind a mass-balance error no edit can clear.

So `job_edit` widens those three sections. It collects the pks the job already
uses, keyed by section prefix, from the line items and machine usages it has
already loaded; `_section_form_kwargs` hands them to the row forms as `keep`; and `_offer_recorded` in `forms.py` unions
them back into the field's queryset. **Only what this job names comes back** — the
rest of the retired catalog stays hidden, and a fresh form passes no `keep` at
all, which is what keeps retiring something meaningful. The workers section needs
none of this: `collaborator_queryset` never filters on `is_active`.

### Resizing a section

Two mechanisms, one of which is allowed to fail. The server round-trip below is
the whole feature and is described first because it is the one that is always
there; `static/js/job_rows.js` then does the same thing without a page load,
and is covered at the end.

A formset renders a fixed number of rows. Before the row buttons, that made the
entry form a hard cap on what could be recorded: three consumed rows, three
produced, four machines, three collaborators, and no way past any of them. A job
crushing one input into four output fractions did not fit, and because the mass
balance is checked across a single submission it could not be split over two
either.

Each section carries two ordinary submit buttons, `add_<prefix>` and
`remove_<prefix>`. The sections live in `JOB_SECTIONS` in `workorders/forms.py`, a
tuple of `JobSection` — prefix, context name, title, row form and the formset
class holding that section's no-duplicates rule — which `_job_form_fields.html`
loops over, and which is the only place a fifth section would have to be
registered.

Both `transform_create` and `job_edit` build their forms through one helper,
`_job_forms`. It checks `_pressed_row_button` ahead of the submission branch,
which reports which section and which direction as `(prefix, delta)`, and then
rebuilds every form on the page out of the raw POST, giving the named section one
row more or less and every other section exactly the rows it already had. Any
other POST comes back bound, and `_valid_job_rows` runs the five validations and
the mass balance before either view writes.

**Removing takes the last row — the exact one adding appends**, so the two
buttons undo each other. Anything cleverer, like dropping the last *empty* row,
would make the button hard to predict from looking at it, and nothing here is
saved: what is on screen is the whole state.

**No section falls below `MIN_ROWS_PER_SECTION` (1).** `_resized_section` applies
that floor to every section on every rebuild, not only the one whose button was
pressed — so neither a „− odebrat řádek" that arrives twice (a double tap on a
slow connection) nor a hand-edited `TOTAL_FORMS` can leave a heading with no row
under it and only the add button as the way back. The template also hides
„− odebrat řádek" once a section is down to one row, so it is never shown as a
control that does nothing; that is presentation, and the view holds the floor
regardless of what arrives. It is rendered carrying the `hidden` attribute
rather than left out, because the script toggles it back when it grows the
section — which means the stylesheet has to make `hidden` stick. `button, .btn`
sets a `display`, and an author rule beats the UA stylesheet's
`[hidden] { display: none }` at any specificity, so `app.css` carries an
explicit `button[hidden], .btn[hidden] { display: none }`. Without it the
attribute does nothing at all and a one-row section shows a button that cannot
lawfully work; `RowTemplateTests` pins the rule.

**The rebuilt page is unbound, and that is the point.** Asking for another row is
not submitting the form, so the page must come back carrying what was typed and
complaining about nothing. A bound rebuild would render *„Toto pole je
vyžadováno."* over the hours box of somebody who has not reached it yet, and the
mass-balance check would object to a form still being filled in. Raw POST strings
round-trip through an unbound widget without help: a pk string re-selects its
option, and a date string reaches `<input type="date">` unchanged instead of
going out through the `cs` `DATE_INPUT_FORMATS` as `05.09.2026`, which the widget
rejects and shows blank — the same trap `WorkOrderForm.performed_on` carries an
explicit `format` for.

Four details are load-bearing and easy to undo by accident:

- **Both buttons carry `formnovalidate`.** *„Moje hodiny"* and *„Datum provedení"*
  are required, so without it the browser blocks the submit and the buttons do
  nothing until the rest of the form happens to be complete — which is exactly
  when nobody needs to change the number of rows.
- **`job_row_formset` builds a class per call.** `extra` is a class attribute, so
  a formset sized to its caller cannot take the size as a constructor argument.
  It is how every formset on the job page is built; there are no module-level
  formset classes to reach for instead.
- **Both callers open their `<form>` with an off-screen decoy submit.** A browser
  submits a form through its *first* submit button when Enter is pressed in a text
  field, and „+ další řádek" comes before the real button. The decoy is
  `.visually-hidden`, has no `name` so it posts nothing, and is out of the tab
  order and hidden from assistive tech.
- **`TOTAL_FORMS` is untrusted on this path.** It is read straight out of the POST
  rather than through a management form, so it is parsed defensively and clamped
  between `MIN_ROWS_PER_SECTION` and `MAX_ROWS_PER_SECTION` (1000, which is what
  the management form already advertises as `MAX_NUM_FORMS`). At exactly the cap
  the new blank row is dropped, because Django will not add an extra beyond its
  own `max_num` either.

`RowButtonTests` in `workorders/tests.py` covers the lot: that only the named
section changes size, that removing takes the last row and never the last one
standing, that the remove button disappears at the floor, that the typed values
and the chosen author come back, that the date stays ISO, that nothing is written
and nothing is scolded, and that the decoy button precedes the first
„+ další řádek" in the rendered page.

#### The script on top of it

`static/js/job_rows.js` intercepts those two clicks and resizes the section in
the DOM instead. It is **enhancement only**: the buttons stay real submits, and
with the script missing, blocked or broken every paragraph above still
describes what happens. That is not courtesy — the suite drives views through
`self.client` and runs no browser, so the server path is the only one it can
see, and the script is deliberately kept to what can go wrong without taking
correctness with it.

It builds no markup. Each section renders Django's own `formset.empty_form`
into a `<template>`, so the widgets, placeholders, option lists and
`empty_label`s still come from `workorders/forms.py`; adding a row is that
template with `__prefix__` swapped for the next index, plus a `TOTAL_FORMS`
increment. `empty_form` is constructed from the formset's `form_kwargs`, which
is what gets the workers template row the right `collaborator_queryset(user,
viewer)` pair and the catalog rows their `keep` — no queryset logic is restated
in JavaScript. It is also *not* narrowed by `_hide_taken_choices`, which only
touches `formset.forms`, so the template carries the whole catalog and the
script hides from it live.

Four things it must keep agreeing with the server about:

- **Removing takes the last row**, the same one adding appends, so indices stay
  contiguous `0..TOTAL_FORMS-1` and the two paths cannot disagree about which
  row went.
- **The floor and the cap are rendered, not restated.** `_job_context` puts
  `MIN_ROWS_PER_SECTION` / `MAX_ROWS_PER_SECTION` in the context and each
  section carries them as `data-min-rows` / `data-max-rows`.
- **A row and its errors are wrapped together** in `.row-block`, so removing
  the last row takes its messages with it rather than orphaning them.
- **The no-duplicates rule is applied per section**, by hiding and disabling
  options other rows took — never by rewriting anything the server validates.
  `UniqueChoiceFormSet.clean()` remains the backstop, and the asymmetry this
  creates is described under the duplicate rule above.

`RowTemplateTests` in `workorders/tests.py` covers the contract rather than the
script: that every section renders a template with `__prefix__` names, that the
bounds are rendered and match the constants, that `empty_form` escapes the
hiding and honours `form_kwargs` and `keep`, that both job pages link the file,
that it names no external URL, and that `app.css` really hides a `hidden`
button. The script's own behaviour is not tested and would need a browser
runner this project does not have — which is the reason it is only ever allowed
to be an optimisation.

### Searching a dropdown

`static/js/searchable_select.js` is the second script, and it is the same
bargain as the first. The depot's material catalog is long enough that finding
a fraction in a dropdown means scrolling past a dozen others, so each row's
`<select>` is hidden behind a text box that narrows the options as you type.
The four sections — spolupracovníci, spotřebováno, vyrobeno, stroje — are all
of it; no other dropdown in the app is enhanced, because no other one is long.

**The `<select>` is still what the form posts.** It keeps its `name`, its
`value` and its place in the page; `hidden` is not `disabled`, so it is
submitted exactly as before, and the text box has no `name` and posts nothing.
Nothing in `workorders/views.py` or `workorders/forms.py` knows this file
exists. With it missing, blocked or broken every row is the plain dropdown it
always was — the same arrangement job_rows.js is held to, and for the same
reason: the suite drives views through `self.client` and runs no browser.

It invents no choices either. The options, their labels, their order and the
`empty_label` that becomes the box's placeholder are all read off the rendered
`<select>`, and read again every time the list opens. That is what makes it
agree with `_hide_taken_choices` and with job_rows.js for free: an option
another row took is `hidden` by the time the list is built, so it is not
offered, and a row always keeps its own. Committing a pick writes the value to
the select and dispatches a `change` event, which is what job_rows.js listens
for — so choosing through the box re-hides the option elsewhere exactly as
choosing from the dropdown did.

Four smaller decisions worth knowing:

- **Matching is case- and diacritic-insensitive**, folded through
  `normalize('NFD')`, because „ster" has to find „Štěrk" from a phone keyboard.
  Matches are substrings, kept in the catalog order the depot already knows.
- **An emptied box blanks the row.** A half-typed one reverts on the way out,
  so the text can never disagree with what the select holds; tabbing away with
  the list open takes the highlighted row, which is what it has been offering
  all along.
- **Rows added by job_rows.js are found with a `MutationObserver`** on the
  section, not by a call from the other file, so neither script depends on the
  other being loaded — or loaded first. `<template>` contents are a separate
  document fragment and are not matched by `querySelectorAll`, so the clone
  source stays exactly as Django rendered it.
- **Enter is left alone when the list is closed**, so it still saves the job
  through the decoy submit; with the list open it takes the highlighted option
  instead of submitting a form the reader has not finished.

`SearchablePickerTests` in `workorders/tests.py` covers the contract rather
than the script, the same way `RowTemplateTests` does: that a row has exactly
one `<select>` for it to find, that the first option is the valueless
`empty_label` it lifts out as the placeholder, that each select carries the id
the listbox is keyed off, and that `app.css` carries the three rules without
which the list renders as a bullet list shoving the rows below it down.

### `WorkerHours` vs `MachineUsage`

Two unrelated numbers. Do not derive one from the other.

- **`WorkerHours`** is labour: what a person typed for themselves. The
  submitter's own hours come from `WorkOrderForm.hours` (*„Moje hodiny"*, and it
  is required); each collaborator's come from a row in the workers section. A
  `UniqueConstraint` allows one row per person per job, and the form refuses a
  second row for someone already named rather than reaching it — see *One row
  each* above.
- **`MachineUsage.hours`** is motohodiny — machine runtime.

The Hodiny report totals `WorkerHours` only, so it has no fixed relationship to
the motohodiny on Stroje. That is not a reconciliation bug.

`MachineUsage.tons` is a third independent number: how much material that one
machine put through on that job. **It is not part of the mass balance.** Chained
machines each handle the same material, so the tonnage column sums to a multiple
of the job's consumed total, not to it — nothing compares the two. The Transform
form requires it on any machine row that records motohodiny, but the column is
nullable because rows written before it existed have no answer (unknown, not
zero), and the history table renders those as a dash.

`MachineRefuel.litres` is a fourth, and it lives in its own table.

### `MachineRefuel`: fuel is not runtime

One machine's fill-up on one job, in litres. Like every other row hanging off a
job it has no timestamp of its own — its date is the job's `performed_on`,
because `job_edit` deletes and rewrites every row and an insert time would drift
to the day of a correction.

**It is a separate table rather than a `litres` column on `MachineUsage`**, and
the reason is not tidiness: the two rows do not imply each other. A machine can
be refuelled on a job it did not run — a whole job can be nothing but tanking up
— so a fill-up has to be able to exist with no usage row beside it. A nullable
column on `MachineUsage` would have meant inventing a usage row with no hours to
hang the litres off, and `hours` is not nullable.

Nothing derives litres from motohodiny or tonnage, and nothing compares them.
Unlike `tons`, `litres` is **not nullable**: the column is new, but a fill-up
row only ever exists because somebody typed a number into it, so there is no
*unknown* to represent and a total of zero is a real zero.

### One machine row, two halves

Both live on the Transform form's *Použité stroje* section, which is now four
fields wide: stroj, motohodiny, tuny, natankováno (l). `MachineUsageForm` is
therefore the one row form that does **not** follow `RowForm`'s all-or-nothing
rule, and overrides `check_row` instead of it:

| Row | Means |
|---|---|
| everything blank | no row |
| machine + hours + tons | a `MachineUsage` |
| machine + litres | a `MachineRefuel`, and no usage row |
| machine + hours + tons + litres | both, from one row |
| machine + one of hours/tons | refused — the usage half is still all-or-nothing |
| machine alone | refused — it says nothing |
| litres with no machine | refused |

`RowForm.clean()` keeps the early return on `self.errors` that stops the row's
own complaint printing over a field error; only the rule itself moved into an
overridable hook.

The no-duplicates rule is unchanged and covers both halves at once: a machine
named twice is refused, so a machine refuelled twice on one job is one row
carrying the sum. `_collect_rows` splits the section's rows into `JobRows.usages`
(those with motohodiny) and `JobRows.refuels` (those with litres), and
`_write_job_rows` writes each list to its own table.

`job_edit` has to put them back together: `_machine_section_rows(usages, refuels)`
merges the two tables into one row per machine, since the section refuses a
machine twice. `keep` collects the machines named by *either* table, or a retired
machine that only a fill-up names would render with nothing selected and the
fill-up would be dropped on save while the page reported success — the same
data-loss trap the usage rows carry.

### A job that is only fuel

**A submission carrying nothing but fill-ups is a complete, valid job.** Someone
tanks up three machines and records that; there is no transformation involved.
`_is_fuel_only(rows)` is true when there is at least one fill-up and no consumed
rows, no produced rows, no motohodiny and no collaborator hours, and such a job
is exempt from three things that every other submission must answer:

- **the mass balance**, because there are no consumed and produced totals to
  compare — not two totals that happen to match;
- **„Popis"**, the job's label;
- **„Lokace"**, where it happened (one typed in is kept);
- **„Moje hodiny"**, which could not be answered honestly anyway: its minimum is
  half an hour, so there is no way to say "I did not work on this". No
  `WorkerHours` row is written for the author at all, which is what `own_hours`
  being `None` means in `_write_job_rows`.

It stays an ordinary `WorkOrder` in every other respect: dated, authored,
`PENDING` until a manager approves it, listed on Přehled, and shown on the job
detail page. Not being *asked* for a description is not the same as refusing
one — a worker who types one keeps it, and the four list columns read it.

Add any motohodiny or any material row and the exemption is gone: the submission
records work, and the balance and both fields come back.

### `collaborators`

**Derived, not entered.** `transform_create` calls
`work_order.collaborators.set(worker_hours.keys())`, so the M2M can never
disagree with the hours rows about who worked the job. Its functional effect is
visibility: a collaborator sees the job in their own history and hours.

`collaborator_queryset(user, viewer)` decides who may be named — never the
job's author (they are already its creator), and a plain worker filling in their
own job may only name other workers. See *Recording for somebody else* above for
what the second argument is doing.

The one place the two can drift is the Django admin: `WorkOrderAdmin` exposes
the `collaborators` multi-select and the `WorkerHours` inline as independent
widgets, so an admin edit can leave a collaborator with no hours row, or a
person with hours who is not a collaborator.

### Machine totals are derived, never stored

`Machine` carries no running counter. Every figure on Stroje is an annotation
over the rows the page's filter allows: `Sum('usages__hours')` and
`Sum('usages__tons')` from `MachineUsage`, and `Sum('refuels__litres')` with
`Count('refuels')` from `MachineRefuel`.

There used to be a `Machine.total_hours` column, maintained by
`MachineUsage.save()`/`.delete()` with `F()` expressions and a `select_for_update()`
on the old row. It came with a standing rule — never `bulk_create`,
`queryset.update()` or `queryset.delete()` a usage row, and never let a
`WorkOrder` cascade onto one — because any of those silently desynchronised it.
It also counted jobs that were still waiting for approval, so it never agreed
with the page anyway. **No page read it**, so the column and the model methods
were dropped. `MachineUsage` is now an ordinary
model, and `_write_job_rows` just bulk-deletes its rows.

Every row hanging off a job — line items, machine usage, fill-ups, worker
hours — cascades when the job is deleted, so `job_delete` is a plain
`work_order.delete()` and the admin's delete page and „delete selected" action
do the same thing. The line items' FK used to be `PROTECT`, left over
from when they were stock history, which made the admin refuse to delete any
job with materials on it. `AdminJobDeleteTests`
covers both admin paths.

`tons` is nullable (rows predating the column mean *unknown*, not zero) and
`Sum` skips NULLs, so a machine with no recorded tonnage sums to `None` and
renders as a dash; `0 t` would claim it processed nothing.

### Stroje is one page, laid out like Hodiny

`machine_dashboard` is a filter, a per-machine summary, and the usage rows the
summary is made of — the same three-part shape as `time_worked`, for the same
reason: those are one dataset at two zoom levels, so a reader can see a number
and then what it consists of without changing page and re-entering the filter.
The old `/machines/history/` page was exactly the bottom third of it and is
gone.

Fuel repeats that shape below it as a second pair of cards, *Tankování* and
*Detail tankování*, under the same filter — see
[The fuel card](#the-fuel-card).

`_machine_summary(usages, form)` totals the *filtered* rows
(`usages__in=usages.values('pk')`), so the two tables can never disagree. Two
rules that differ from the summary on Hodiny, where a person with no hours in
range simply drops out:

- **Every active machine is listed**, even at zero. With no filter that is the
  whole fleet; under a date filter, a machine sitting at `0 h` is the answer to
  "what ran last week".
- **Naming a machine in the filter narrows the table to it**, since the rest
  would be a column of zeros nobody asked for.

An invalid filter returns nothing — the same rule the rows follow, and for the
same reason: a fleet of zeros under a "these are your filtered results" heading
reads as an answer.

#### What the machine hours and tonnes cost

`Machine` carries two optional rates, `hourly_rate` (Kč/hod) and `rate_per_ton`
(Kč/t). They were reference-only until the summary grew three money columns:

| Column | Value |
|---|---|
| **Cena za hodiny** | `filtered_hours × hourly_rate` |
| **Cena za tuny** | `filtered_tons × rate_per_ton` |
| **Celkem** | the sides the machine is priced on, added up |

`_machine_costs(machine)` computes them per row, which is why
`_machine_summary` returns a **list** rather than a queryset: the rule below is
conditional, and a `Case`/`When` over an aggregate would be much harder to read
than a loop over a table of one row per active machine.

Both rules come down to telling *unknown* from *zero* — the distinction a
nullable `MachineUsage.tons` already forces on this page:

- **An unset rate means the machine is not priced that way. It does not mean
  free.** That side of the bill is unknown and renders as a dash, exactly as
  the rate column beside it does. But `filtered_hours` is a real zero, so a
  machine that *is* priced by the hour and did not run in range costs `0,00`,
  not a dash.
- **„Celkem" is unknown when any priced side is.** A machine billed per tonne
  whose usage rows predate the `tons` column has a cost nobody can compute;
  printing the hours half of it under a „Celkem" heading would understate the
  bill, which is worse than admitting the number is unavailable. A machine
  priced on one side only totals to that side — the unpriced side is out of the
  sum, not zero in it.

The money follows the page's filter like everything else on it: these are the
filtered rows, priced. In the CSV export all three go through `_csv_number`, so
an unknown is a blank cell rather than a `0` that Excel would sum as a machine
that cost nothing.

#### The fuel card

*Tankování* is `_refuel_summary(refuels, form)`: litres and fill-up count per
machine over the rows the page's own filter allows
(`refuels__in=refuels.values('pk')`), with a *Celkem* row under it. It repeats
`_machine_summary`'s two rules deliberately — every active machine is listed, and
naming one in the filter narrows the card to it — because "which machines did we
fuel last week" and "which ran" are the same question about the same fleet, and a
machine at `0 l` answers it.

It differs from the card above it in one way: **the zeros are real zeros.**
`litres` is not nullable, so a `Sum` coming back `None` can only mean "no
fill-ups in range" and is `Coalesce`d to `0` — there is no *unknown* to render as
a dash the way `tons` has. That also lets `_refuel_summary` stay a queryset,
where `_machine_summary` has to be a list for `_machine_costs`.

`_filtered_machine_refuels` applies `MachineFilterForm` unchanged: its three
lookups (`machine`, `work_order__created_by`, `work_order__performed_on`) read
identically on `MachineRefuel`, so the fuel card and the usage card cannot
disagree about what the filter means. Both are approved-only, and an invalid
filter returns `Machine.objects.none()` and a total of `0`.

**Stroje is the one page with two paginated tables**, so each carries its own
page parameter — `_paginated(request, rows, param)` returns `page_obj`, the
`querystring` its links carry, and `page_param`, dropping only *that* table's
parameter and keeping the filters and the other table's page. `_list_page_context`
is now that helper plus `date_presets`, and `_pagination.html` reads `page_param`
rather than writing `page` into the link. `_date_preset_links` drops both, since
a new date range starts at page one in either table. One shared `page` would have
moved both tables at once and made one of the two links lie.

### Materiál is the same page with `Material` in place of `Machine`

`material_dashboard` is Stroje's shape applied to the line items: a filter, a
per-material summary, and the `StockMovement` rows the summary is made of. It
is **the only place the line items are read back across jobs** — `job_detail`
shows them one job at a time, which cannot answer "how much 8/16 did we make
last month?". Before this page existed, that question had to go through the
Django admin.

`_material_summary(movements, form)` totals the *filtered* rows
(`movements__in=movements.values('pk')`) exactly as `_machine_summary` does,
and inherits the same two rules: every **active** material is listed even at
zero, and naming one in the filter narrows the table to it. An invalid filter
returns `Material.objects.none()`.

Three columns, and two of them fall out of how `quantity` is stored:

| Column | Annotation |
|---|---|
| Spotřebováno | `Abs(Sum(...))` over the `TRANSFORM_CONSUME` rows |
| Vyrobeno | `Sum(...)` over the `TRANSFORM_PRODUCE` rows |
| Rozdíl | `Coalesce(Sum(...), 0)` over **all** the rows |

Because `quantity` is signed, the net is simply the unfiltered sum — no
subtraction and no second query. It is the column that matters for a material
that is both an input and an output: gravel crushed into a fraction on one job
and fed back in on another nets out to what actually accumulated. The `Abs` on
the consumed side is the queryset equivalent of `_job_line_items`'s `abs()` —
the form asked for a positive number, and that is what the reader should see.

One difference from Stroje: **a zero here is a real zero**, not the unknown
that a NULL `MachineUsage.tons` represents. So the net is `Coalesce`d to `0`
and the two sides render through `|default:"0"`, and a material with no rows in
range reads `0,00 t` rather than a dash.

`MaterialFilterForm` deliberately has no `created_by`, unlike
`MachineFilterForm`: who typed a job in is a review question, and Přehled
answers it. Its queryset is every material rather than the active ones, because
narrowing to a retired material is the only way to see its history — the
summary above is what restricts itself to `is_active=True`.

### Each exported table downloads as a CSV

Hodiny, Stroje and Materiál each carry a „Stáhnout do CSV / Excelu" button at
the foot of their **summary** card — „Souhrn", „Stav strojů", „Souhrn
materiálů" — Stroje carries a second one under „Tankování", its other summary,
and Materiál carries one under „Detail položek".

**That detail table is the one exception, and it is deliberate.** Everywhere
else the paginated rows underneath a summary are the working-out and the
summary is the report. Materiál's rows are not working-out: they are the only
place in the app that says what a single job consumed and produced, one line at
a time, which is what a manager reconciling a month against delivery notes
actually needs. Stroje's usage rows have no such reading — the totals *are* the
question there — so they stay unexported, and so do the fill-ups.

**The detail file is every row the filter allows, not the page on screen.** The
table paginates at `HISTORY_PAGE_SIZE`; a file holding rows 1–50 of 300 under
the heading of the whole filter would be worse than no file. The summaries have
no equivalent trap, being one row per material or machine.

The link carries the page's own `querystring`, so the file is the table that
was on screen — pick "minulý měsíc", then download last month. Each export view
is built from the *same* helpers as its page, which is what makes that true
rather than merely intended:

| Export | Name | Built from |
|---|---|---|
| „Stav strojů" | `machine_dashboard_export` | `_filtered_machine_usages` + `_machine_summary` |
| „Tankování" | `machine_refuel_export` | `_filtered_machine_refuels` + `_refuel_summary` |
| „Souhrn materiálů" | `material_dashboard_export` | `_filtered_material_movements` + `_material_summary` |
| „Detail položek" | `material_detail_export` | `_filtered_material_movements` |
| „Souhrn" (hodiny) | `time_worked_export` | `_time_worked_scope` |

`_time_worked_scope` was extracted out of `time_worked` for this: Hodiny scopes
by participation as well as by the filter, and an export that scoped a worker
differently from the table they started it from would hand them the depot's
hours. With the scoping in the shared helper there is one rule, not two. The
rest follows from reusing the helpers — a pending job is out of the file
exactly as it is out of the page, and an invalid filter exports a header row
and nothing else.

Each export carries the same gate as its page: `time_worked_export` is
`login_required` (a worker downloads their own row), the other four are
`role_required(MANAGER, ADMIN)`. The detail file is the one most worth gating —
it names every job's author and description, not just totals.

**The file format answers "CSV or Excel" once, and adds no dependency.**
`_csv_response` writes `;`-delimited rows behind a UTF-8 BOM:

- **`;`** because Excel splits a `.csv` on the *locale's* list separator, and
  under `cs` that is the semicolon. A comma-delimited file opens in one column.
  It also leaves the comma free to be the decimal separator.
- **The BOM** is what makes Excel read the file as UTF-8. Without it, „Štěrk"
  opens as mojibake. Browsers and LibreOffice ignore it, and Python reads it
  back with `encoding='utf-8-sig'`.
- **Numbers go through `number_format`**, so a tonnage is `12,50` — a number
  Excel can sum under `cs`, not text. Python code does not localise itself; see
  [localization.md](localization.md).
- **An unknown exports blank, not `—`.** A NULL `MachineUsage.tons` or an unset
  rate renders as a dash on the page, but a dash in a spreadsheet cell is text
  that breaks a column of numbers; blank stays out of a `SUM`. A real zero is
  passed in by the caller, matching the page's `|default:"0"`. The same applies
  to text: a job with no description — one recorded before „Popis" became
  required — exports an empty cell rather than the page's `—`, which would
  otherwise be a value the reader has to filter around.
- **Dates go out ISO**, as `performed_on.isoformat()`, which is what the
  „Provedeno" column already renders. Template dates are written with an
  explicit format for exactly this reason (see
  [localization.md](localization.md)), and Excel under `cs` reads an ISO date
  as a date.

openpyxl would buy cell formatting nobody asked for and a dependency the app
otherwise does without.

## Pages and URLs

All in `workorders`, all mounted at the **root** by `config/urls.py`. No other
app contributes a URL: `accounts`, `materials` and `machines` are
model-and-admin only.

The views are split across two modules. `workorders/views.py` records and
reviews jobs — Zpracování and the `/jobs/` pages. `workorders/reports.py` holds
Hodiny, Stroje and Materiál, their CSV exports, and the list-page helpers
(`_list_page_context` and the date presets) that `job_dashboard` imports from
it. `REVIEWER_ROLES`, which both modules gate on, lives in
`accounts/decorators.py` beside `role_required`.

**The reports are a module, not an app, on purpose.** Splitting them into a
`reports` app was considered and rejected: every report only aggregates
`WorkOrder`, `StockMovement`, `WorkerHours` and `MachineUsage` and counts
approved jobs alone, their filter forms live in `workorders/forms.py`, and
`job_dashboard` imports `_list_page_context` from `reports.py`. A separate app
would depend on `workorders` completely, and the shared list helpers would have
to move out first to avoid a circular import — all for no change in behaviour.
The module split already separates recording jobs from reading them back. The
same reasoning keeps `MachineUsage` in `workorders` rather than `machines`: it
is a row of a job, deleted with it and dated by it, not catalog data. Revisit
the reports only if they get a separate audience or deployment.

| URL | Name | What |
|---|---|---|
| `/` | `transform_create` | Zpracování — the form, and the landing page |
| `/hours/` | `time_worked` | Hodiny |
| `/machines/` | `machine_dashboard` | Stroje — filter, per-machine totals, usage rows |
| `/materials/` | `material_dashboard` | Materiál — filter, per-material tonnage, line items |
| `/jobs/` | `job_dashboard` | Přehled — every job, review state highlighted |
| `/jobs/<pk>/` | `job_detail` | One job in full, with the review actions |
| `/jobs/<pk>/upravit/` | `job_edit` | Correct a recorded job |
| `/jobs/<pk>/schvalit/` | `job_approve` | POST only |
| `/jobs/<pk>/smazat/` | `job_delete` | GET confirms, POST deletes |
| `/hours/export/` | `time_worked_export` | The Hodiny summary as a CSV |
| `/machines/export/` | `machine_dashboard_export` | „Stav strojů" as a CSV |
| `/materials/export/` | `material_dashboard_export` | „Souhrn materiálů" as a CSV |

The five `/jobs/` views are the review pages, and every one of them carries
`@role_required(MANAGER, ADMIN)`.

The three `/export/` URLs are downloads rather than pages: no template, no nav
entry, and each gated exactly like the page it hangs off.

`LOGIN_REDIRECT_URL`, the header logo and the post-submit redirect all point at
`transform_create`. Login and password change are Django's own generic views,
wired in `config/urls.py` with Czech form subclasses from `accounts/forms.py`;
`accounts` has no `views.py` at all, and neither do `materials` or `machines` —
the first two held nothing but `# Create your views here.` and were removed.

## Roles and permissions

`accounts.User` extends `AbstractUser` with a `role` field.

| Role | `is_staff` / `is_superuser` | App access |
|---|---|---|
| `WORKER` | no | Zpracování, Hodiny; own records only; submissions await approval |
| `MANAGER` | no | + Přehled (approve/edit/delete), + Stroje, + Materiál, + everyone's records |
| `ADMIN` | **yes / yes** | + Django admin |

Three gates:

- `accounts/decorators.py::role_required(*roles)` on the view. Anonymous users
  get the login redirect; authenticated users with the wrong role get a 403 —
  not a bounce back to login, which would be a confusing dead end.
- `User.is_manager_or_admin` for conditional UI and query scoping.
- `accounts/middleware.py::AdminSessionRequiredMiddleware` on `/admin/` itself,
  below.

**The `/jobs/` review views, `machine_dashboard` and `material_dashboard` all
use `role_required`** — a worker who types one of those URLs gets a 403, not a
page. Hiding a link is not access control, so Stroje and Materiál are gated in
the view as well as kept out of a worker's nav.

The gate **replaced** Stroje's worker scoping rather than sitting on top of it.
`_filtered_machine_usages` no longer narrows the rows to the viewer's own jobs,
and `MachineFilterForm` no longer takes a `user` or hides its `created_by`
field — everyone who gets past the decorator sees the whole depot, so both were
unreachable. `time_worked` is now the only page that scopes by participation,
because it is the only filtered list a worker can open. Relaxing the gate means
writing both halves back.

**Superusers bypass both gates.** `createsuperuser` never sets a `role`, so a
bootstrap admin would otherwise default to `WORKER` and be locked out of the app
it administers.

**Django's own permission system is not used, and is hidden.** There are no
groups and no per-user permissions; `auth.Group` is unregistered from the admin
in `accounts/admin.py`, and `CustomUserAdmin` sets `fieldsets` without
`groups`/`user_permissions`. Anything reachable in the admin is reachable by a
superuser, who bypasses permission checks entirely, so a group would have been a
no-op that looked like access control. Both gates above are the real ones.

Managers deliberately get **no** Django admin access. Everything they need is in
the app itself; the admin is a back office for editing the catalog and accounts,
where an accidental edit has no audit trail.

`ADMIN` accounts get `is_staff` **and** `is_superuser`. `is_staff` alone permits
logging in to `/admin/` and then shows an empty "you don't have permission"
index — worse than no access at all. Both `seed_data` and
`CustomUserAdmin.save_model` derive the two flags from `role`, so they stay
consistent however the account was created.

### The admin is a 404 unless you are already an admin

The deployment is a public VPS. Django's admin answers its own login form to
anonymous requests, which puts a password form — the superuser one — at the URL
every scanner on the internet tries first.
`accounts/middleware.py::AdminSessionRequiredMiddleware` answers **404** for
everything under the admin prefix unless the request already carries an admin
session:

```python
@property
def has_admin_access(self):
    return self.is_active and self.is_staff and (self.is_superuser or self.role == self.Role.ADMIN)
```

Being middleware, it covers every admin URL at once rather than depending on
each `ModelAdmin` — `/admin/login/` included, which is the point: there is no
admin login form on the internet at all, leaving the app's own `/login/`, which
django-axes rate-limits, as the only one. The way in is to log in to the app
first and then open `/admin/`; an admin already working in the app just follows
the header link.

Five details worth keeping:

- **404 rather than a redirect to `/login/`.** A redirect would be friendlier to
  a logged-out admin — and would confirm to a scanner that the admin is here.
  The admin is one visit to the app away either way, so the trade goes to
  hiding it. The cost is that `/admin/` typed while logged out looks like a
  broken URL; the runbook says to expect that.
- **Its position in `MIDDLEWARE` is load-bearing.** It goes *above*
  `CommonMiddleware`: `APPEND_SLASH` turns any 404 for `/admin` into a 301 to
  `/admin/`, which hands back exactly the confirmation the 404 withholds. Above
  `CsrfViewMiddleware` too, so an anonymous POST to `/admin/login/` is refused
  as a 404 rather than a 403. That puts it above `AuthenticationMiddleware`, so
  it resolves the user itself with `django.contrib.auth.get_user` — precisely
  what that middleware's lazy `request.user` calls — and needs only
  `SessionMiddleware` above it. Moving it down breaks the slashless case
  silently, so both the suite and `scripts/smoke_prod_stack.sh` request
  `/admin` without its slash.
- **The prefix is read from `reverse('admin:index')`**, not hardcoded, so
  mounting the admin somewhere else stays covered. Lazily, on the first request:
  `reverse()` needs the URLconf loaded, which it is not while middleware is
  being instantiated.
- **The user is resolved only for paths under the prefix**, so no other request
  in the app pays for a session lookup it would not otherwise make.
- **The header link reads the same property.** `base.html` shows
  „Administrace“ on `user.has_admin_access`, not on `is_staff`, so a visible
  link can never lead to the 404 — a manager given `is_staff` by hand sees no
  link and gets no admin, which is the rule the role table above already
  stated.

What it does **not** do is make the admin a secret: `collectstatic` publishes
Django's own admin CSS at `/static/admin/`, so a determined scanner can still
tell the app has one. The gate is access control — there is no admin login form
to guess against — and the hiding is a side benefit, not something to lean on.
The `Caddyfile`'s commented source-IP block and a VPN are still the answers if
the admin must be unreachable even to a stolen session.

`AdminGateTests` and `AdminLinkTests` in `accounts/tests.py` cover it, and
`scripts/smoke_prod_stack.sh` re-checks the 404 against the built image behind
Caddy, anonymously and as a logged-in non-admin.

### How worker scoping is enforced

**One view scopes its results: `time_worked`.** It is the only filtered list a
worker can open — Stroje and Přehled are both `role_required`, and a page whose
every visitor is a manager has nothing to scope. It does the job **twice**, and
the two are not equivalent:

```python
# In the view — this is the real enforcement.
if not request.user.is_manager_or_admin:
    qs = qs.filter(Q(created_by=user) | Q(collaborators=user)).distinct()

# In the form's __init__ — cosmetic only.
if not user.is_manager_or_admin:
    del self.fields['worker']
```

Deleting the field just removes a dropdown that would be a dead end. The
**queryset filter** is what makes `?worker=<someone-else>` a no-op, and
`test_worker_cannot_bypass_restriction_via_worker_param` asserts exactly that.
Keep both when adding a view a worker can reach; the enforcement must not depend
on the form. Don't add either half to a `role_required` view — `machine_dashboard`
carried both until it was gated, and they became code no request could execute.

`time_worked` needs a **third** step on top: after aggregating, it filters the
summary rows to the requesting worker. Without that, a job someone else created
and invited them to would put the creator's hours on their screen.

## Filter forms

All four list views — Stroje, Materiál, Hodiny and the job dashboard — follow
one pattern, and the edge cases are the point:

| Request | Behaviour |
|---|---|
| No querystring at all (`form.is_bound` false) | Show everything in scope. |
| Valid filters | Apply them. |
| **Invalid** filters | Return **`.none()`**. |

The last row is deliberate. Silently ignoring a bad filter would hand back
everything under a heading that says "these are your filtered results" — the
most dangerous possible response, because it looks like an answer. The template
renders an explicit warning instead.

Note the ordering: `is_bound` is checked *before* `is_valid()`, because an
unbound form is also not valid, and conflating them would return nothing on a
plain page load.

All three rows live in one place, `DateRangeFilterForm.filter(queryset)` in
`workorders/forms.py`. Each filter form declares `lookups` — field name to ORM
lookup string, or to a callable returning a `Q` (how `TimeWorkedFilterForm`
applies `participation_filter`) — and `date_lookup`, which is `performed_on` on
job-level pages and `work_order__performed_on` on the Stroje and Materiál rows.
A view builds its base queryset and calls `form.filter()`; none of them writes
the branches out.

Neither `JobFilterForm` nor `MachineFilterForm` has a worker variant, and
neither takes a `user`: the pages they filter are manager/admin only in the
view, so there is no field to hide and no scoping to double up on.
`TimeWorkedFilterForm` is the one that still does.

All three subclass **`DateRangeFilterForm`**, which holds the `date_from` /
`date_to` pair and the „od ≤ do" check — every filtered page narrows by a span
of days on top of whatever else it scopes by, so that rule lives in one place.
Each subclass declares only its own fields, plus a `field_order`: `{{ form.as_p }}`
renders in declaration order and inherited fields come first, so without it the
dates would jump above the picker they qualify.

### Quick date ranges

Every filter card opens with a row of quick ranges — **Vše**, *posledních 7 dní*,
*posledních 30 dní*, *minulý měsíc* — from `DATE_PRESETS` and
`_date_preset_links(request)` in `workorders/reports.py`, rendered by
`templates/workorders/_date_presets.html`. Each list page includes it through
`_filter_card.html` (the invalid-filter warning, the presets and the form), and
pages its rows with `_pagination.html`.

They are plain links that set `date_from`/`date_to` in the querystring rather
than a field on the form: one tap on a phone, no JS, and since the form is bound
afterwards the range stays visible in the two date inputs. Each link carries the
rest of `request.GET` over, so picking a range does not drop the machine or
worker already chosen, and drops `page`, because a new range starts at page one.
The link whose range is currently in effect renders as the solid button, which
makes the row a read-out as well as a control.

Dates go in as ISO. Czech `DATE_INPUT_FORMATS` is `%d.%m.%Y`-first and does not
list ISO, but Django appends `%Y-%m-%d` to every locale's list, so they parse —
and `<input type="date">` accepts nothing else anyway. The spans include today,
so „posledních 7 dní" is today plus the six before it; „Vše" removes both
parameters, which is the way back out of a range without emptying a date input
by hand.

Adding a filtered page means both halves: the context key *and* the include in
the template. The context side is `**_list_page_context(request, rows)` in
`workorders/reports.py`, which returns all three keys a filtered list needs —
`page_obj`, `querystring` (every parameter except `page`, so a page link carries
the filters with it) and `date_presets`. They travel together because a page
wants all three, and picking up two of them is the failure that is hard to
spot: the filter silently disappears on page two.

## Time-worked reporting

`workorders/reports.py::_time_worked_summary` sums `WorkerHours.hours` per person
over the scoped jobs — the hours each person typed for themselves, not a share
of anything.

One implementation detail that is easy to break: the view re-queries by
`pk__in` into a fresh `scoped` queryset before aggregating. The collaborator
filters join the many-to-many table, and aggregating over a joined queryset
multiplies the `Sum` by the number of matched collaborators.

## What is deliberately absent

- **No stock balances.** See the note at the top; this is the big one.
- **No REST API.** `rest_framework` was installed and configured for session
  auth, but there were never any serializers, viewsets or routes. It and the
  `REST_FRAMEWORK` settings block are gone.
- **No JavaScript framework, and two scripts of the app's own — both on the
  same page.** `static/js/job_rows.js` resizes a section of the job form in the
  DOM and `static/js/searchable_select.js` makes its four dropdowns
  type-to-narrow; every other page is a plain form POST with no script at all.
  Both are **progressive enhancement and nothing more** — the searchable
  pickers hide a `<select>` that is still what the form posts, and the
  „+ další řádek" / „− odebrat řádek"
  buttons remain ordinary submits that the server still answers by re-rendering
  the page one row bigger or smaller (see "Resizing a section without
  JavaScript" above, which is still what happens when the script does not run).
  That arrangement is not politeness: the suite drives views through
  `self.client` and runs no browser, so the server path is the only one it can
  see and has to stay correct on its own. `django_htmx` and `widget_tweaks`
  were installed and never used — no template carried an `hx-*` attribute or
  loaded the tag library — so both are gone, along with the htmx middleware and
  the `<script src="https://unpkg.com/htmx.org">` tag in `base.html`. That tag
  was worth removing on its own: the deployment was LAN-only at the time, and
  it made every page load reach for a CDN the depot may not have been able to
  see. (It is a public VPS now, so the CDN is reachable again — which changes
  nothing, because nothing uses htmx.) The rule it stands for survives: the app
  serves its own scripts from `static/`, never a third party's from a CDN, and
  `RowTemplateTests` fails if a URL appears in either file.
- **No CSS framework, and no per-template CSS.** All styling is one file,
  `static/css/app.css`, loaded by `base.html`. No template carries an inline
  `style=` attribute or a `<style>` block. Colours are custom properties
  declared twice — `:root` for light, `@media (prefers-color-scheme: dark)`
  for dark — so dark mode is the same stylesheet with a second set of values,
  chosen by the operating system. There is no theme toggle and nothing is
  stored per user, which is what keeps the bullet above true.
- **No soft deletes.** Catalog entries are retired with `is_active = False`,
  which removes them from every dropdown while preserving their history.
  Deletion is blocked by `on_delete=PROTECT` once anything references them.
  The one exception is deliberate: `job_edit` keeps offering the material or
  machine a job *already names*, however it is flagged, because a picker that
  has dropped the stored value renders the row blank and then throws it away on
  save. See "Editing a job that names a retired record" above.
