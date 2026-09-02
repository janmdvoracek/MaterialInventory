# Architecture

A modular monolith: one Django project (`config`) with four apps that depend on
each other in one direction only — `accounts` and `materials` hold the reference
data, `inventory` records the material line items, and `workorders` groups those
into jobs and owns every page.

```
accounts ──┐
           ├──> inventory ──> workorders
materials ─┘        (StockMovement.work_order → WorkOrder)
```

> **This app does not track stock.** Receipts, shipments, adjustments, the stock
> dashboard, the movement history and its CSV export were all removed, along with
> `Material.track_stock` and every balance and sufficiency check. Nothing sums
> quantities into an on-hand figure. The repository name is historical.

---

## Job line items

`inventory.StockMovement` keeps its name to avoid a table rename, but it is no
longer a ledger. One row is **one material line on one job**: what the job
consumed or what it produced.

| Field | Meaning |
|---|---|
| `material` | What was processed. |
| `quantity` | **Signed.** Negative for consumed, positive for produced. Always tonnes — a material has no unit field, so the `t` shown in the form prompt and the job detail is hardcoded. |
| `movement_type` | `TRANSFORM_CONSUME` or `TRANSFORM_PRODUCE`. Nothing else. |
| `work_order` | The job this line belongs to. **Required.** |
| `created_by` | Who recorded it. |

**A line item has no timestamp of its own.** Its date is its job's
`performed_on`, and `Meta.ordering = ['id']` puts the rows of a job in the order
they were typed. It used to carry both a `created_at` and a `recorded_at`, plus
a `notes` field no form ever wrote; migration `0007` dropped all three because
nothing read them.

The same migration deleted the last two rows carrying the retired `RECEIPT` /
`SHIPMENT` values, which were also the only movements without a job — so
`work_order` stopped being nullable. `MovementType` now describes every row in
the table.

### Not in the admin

`StockMovement` is deliberately **not registered** with the Django admin. A line
item never exists apart from the job it belongs to, and `WorkOrderAdmin` already
edits the rows as an inline, so registering it as well only added a second
top-level section to the admin index — labelled *Zpracování*, the same word as
the worker-facing form, and holding one model already reachable from *Zakázky*.
Everything about a job is edited in one place.

That leaves `inventory` with no admin at all. Its `AppConfig.verbose_name` is
kept for whenever something is registered again, but nothing renders it today.
The `date_hierarchy` that used to live on the movement list moved to
`WorkOrderAdmin`, which is now the only admin that has one — and the only place
the Czech date-hierarchy string from `locale/cs` is exercised.

### What this means for new code

There is no available-quantity helper, no `select_for_update()`, and no check to
perform before writing a line item. A job records what a worker says happened.
If you ever need balances back, you are adding a genuinely new subsystem — read
the git history for `inventory/services.py` rather than assuming any of the old
machinery is still wired up.

## Work orders

A transformation is one `WorkOrder` plus everything it produced:

```
WorkOrder #17  "Crushing gravel"
├── StockMovement  TRANSFORM_CONSUME  −20 t  Raw stone   @ Yard
├── StockMovement  TRANSFORM_PRODUCE  +14 t  Gravel 8/16 @ Yard
├── StockMovement  TRANSFORM_PRODUCE  +6 t   Gravel 4/8  @ Yard
├── MachineUsage   Crusher  3.5 h, 20 t
├── WorkerHours    novak 6 h,  svoboda 4 h
└── collaborators  [svoboda]
```

The consumed and produced sides of that job add up to the same 20 t, and that
is enforced. **A submission must carry at least one consumed row and one
produced row, and their totals must be exactly equal**; otherwise
`transform_create` re-renders the form with an error and writes nothing at all —
not the line items, not the hours, not the machine usage.

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

Formset rows are written **exactly as typed**. The view used to combine consumed
rows for the same material so it could test them as a single quantity; with no
such check left, two rows of 6 t are simply two line items that contribute 12 t
to the consumed total.

### When a job happened

`WorkOrder.performed_on` is the day the work was done; `created_at` is when
somebody typed it in. They differ only when the submitter ticks *„Jiné datum než
dnes"* on the Transform form and picks a date.

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

Migration `0010` added the column with a `timezone.localdate` default and then
back-filled every existing row from `created_at`: before the field existed there
was no way to record a job for any day but the one it was entered, so that date
is the right answer for them.

**Every report keys off `performed_on`.** The date filters on Hodiny, Stroje
and Přehled compare it directly — it is a `DateField`, so a plain `__gte`/`__lte`
with no `__date` lookup and no timezone conversion behind it — and each list's
*Provedeno* column shows it. Ordering is `['-performed_on', '-created_at']` on
all three lists and in `WorkOrder.Meta` (migration `0011`): the day the work
happened, then entry order within a day.

Two consequences worth knowing:

- **Machine-usage rows are dated by their job** (`work_order__performed_on`),
  never by `MachineUsage.created_at`. `job_edit` deletes and rewrites every
  usage row, so a corrected job would otherwise jump to the day it was
  corrected.
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
on, `transform_create` validates `WorkOrderForm` **before** building the
`WorkerHoursFormSet`. An invalid form has no author; the submitter stands in,
only so the page can be re-rendered with its errors.

### Approval

A `WorkOrder` carries a `status` — `PENDING` or `APPROVED` — plus `reviewed_at`
and `reviewed_by`.

**A job counts only once it is approved.** Hodiny, Stroje and the machine-usage
history all report `status=APPROVED` rows and nothing else, so a job a worker
submits is recorded immediately but stays out of the reports until a manager
signs it off. A manager's or admin's own submission is written `APPROVED` with
themselves as the reviewer — there is nobody above them to approve it, so
waiting would leave it stuck forever.

There are only two outcomes, and both are the manager's to carry out: approve
the job, or fix it (`job_edit`) — and if it is beyond fixing, delete it
(`job_delete`). A job is never handed back to its author; **only a manager can
correct a job**. There is no `RETURNED` status and no review note. Migration
`0009` removed both, moving any job that had been returned back to `PENDING`: it
was never approved, so it belongs in the queue awaiting a decision.

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

- It is scoped by `created_by` alone, **not** by `_participation_filter` — being
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

### `WorkerHours` vs `MachineUsage`

Two unrelated numbers. Do not derive one from the other.

- **`WorkerHours`** is labour: what a person typed for themselves. The
  submitter's own hours come from `WorkOrderForm.hours` (*„Moje hodiny"*, and it
  is required); each collaborator's come from a `WorkerHoursFormSet` row. A
  `UniqueConstraint` allows one row per person per job, and naming the same
  person on two rows sums their hours rather than tripping it.
- **`MachineUsage.hours`** is motohodiny — machine runtime.

The Hodiny report totals `WorkerHours` only, so it has no fixed relationship to
the motohodiny on Stroje. That is not a reconciliation bug.

`MachineUsage.tons` is a third independent number: how much material that one
machine put through on that job. **It is not part of the mass balance.** Chained
machines each handle the same material, so the tonnage column sums to a multiple
of the job's consumed total, not to it — nothing compares the two. The Transform
form requires it on any machine row that names a machine, but the column is
nullable because rows written before it existed have no answer (unknown, not
zero), and the history table renders those as a dash.

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

`Machine` carries no running counter. Both figures on Stroje are annotations
over the `MachineUsage` rows the page's filter allows:
`Sum('usages__hours')` and `Sum('usages__tons')`.

There used to be a `Machine.total_hours` column, maintained by
`MachineUsage.save()`/`.delete()` with `F()` expressions and a `select_for_update()`
on the old row. It came with a standing rule — never `bulk_create`,
`queryset.update()` or `queryset.delete()` a usage row, and never let a
`WorkOrder` cascade onto one — because any of those silently desynchronised it.
It also counted jobs that were still waiting for approval, so it never agreed
with the page anyway. **No page read it**, so migration `materials.0011` dropped
the column and the model methods with it. `MachineUsage` is now an ordinary
model, and `_write_job_rows` and `job_delete` just bulk-delete their rows.

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

`_machine_summary(usages, form)` totals the *filtered* rows
(`usages__in=usages.values('pk')`), so the two tables can never disagree. Two
rules that differ from the summary on Hodiny, where a person with no hours in
range simply drops out:

- **Every active machine is listed**, even at zero. With no filter that is the
  whole fleet; under a date filter, a machine sitting at `0 h` is the answer to
  "what ran last week".
- **Naming a machine in the filter narrows the table to it**, since the rest
  would be a column of zeros nobody asked for.

An invalid filter returns `Machine.objects.none()` — the same rule the rows
follow, and for the same reason: a fleet of zeros under a "these are your
filtered results" heading reads as an answer.

## Pages and URLs

All in `workorders`, all mounted at the **root** by `config/urls.py` —
`inventory` contributes no URLs at all.

| URL | Name | What |
|---|---|---|
| `/` | `transform_create` | Zpracování — the form, and the landing page |
| `/hours/` | `time_worked` | Hodiny |
| `/machines/` | `machine_dashboard` | Stroje — filter, per-machine totals, usage rows |
| `/jobs/` | `job_dashboard` | Přehled — every job, review state highlighted |
| `/jobs/<pk>/` | `job_detail` | One job in full, with the review actions |
| `/jobs/<pk>/upravit/` | `job_edit` | Correct a recorded job |
| `/jobs/<pk>/schvalit/` | `job_approve` | POST only |
| `/jobs/<pk>/smazat/` | `job_delete` | GET confirms, POST deletes |

The five `/jobs/` views are the review pages, and every one of them carries
`@role_required(MANAGER, ADMIN)`.

`LOGIN_REDIRECT_URL`, the header logo and the post-submit redirect all point at
`transform_create`. Login and password change are Django's own generic views,
wired in `config/urls.py` with Czech form subclasses from `accounts/forms.py`;
`accounts/views.py` is empty.

## Roles and permissions

`accounts.User` extends `AbstractUser` with a `role` field.

| Role | `is_staff` / `is_superuser` | App access |
|---|---|---|
| `WORKER` | no | Zpracování, Hodiny; own records only; submissions await approval |
| `MANAGER` | no | + Přehled (approve/edit/delete), + Stroje in the nav, + everyone's records |
| `ADMIN` | **yes / yes** | + Django admin |

Two gates:

- `accounts/decorators.py::role_required(*roles)` on the view. Anonymous users
  get the login redirect; authenticated users with the wrong role get a 403 —
  not a bounce back to login, which would be a confusing dead end.
- `User.is_manager_or_admin` for conditional UI and query scoping.

**The `/jobs/` review views all use `role_required`** — a worker who types one of
those URLs gets a 403, not a page. `machine_dashboard` and
`machine_dashboard` still does not: it is hidden from a worker's nav but
reachable by typing the URL. Hiding a link is not access control.

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

### How worker scoping is enforced

Two views scope their results — machine-usage history and time worked. Each does
it **twice**, and the two are not equivalent:

```python
# In the view — this is the real enforcement.
if not request.user.is_manager_or_admin:
    qs = qs.filter(Q(created_by=user) | Q(collaborators=user)).distinct()

# In the form's __init__ — cosmetic only.
if not user.is_manager_or_admin:
    del self.fields['created_by']
```

Deleting the field just removes a dropdown that would be a dead end. The
**queryset filter** is what makes `?created_by=<someone-else>` a no-op. Each
view has a `test_worker_cannot_bypass_restriction_via_*_param` test asserting
exactly that. Keep both when adding a scoped view; the enforcement must not
depend on the form.

`time_worked` needs a **third** step on top: after aggregating, it filters the
summary rows to the requesting worker. Without that, a job someone else created
and invited them to would put the creator's hours on their screen.

## Filter forms

All three list views — machine history, time worked, and the job dashboard —
follow one pattern, and the edge cases are the point:

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

`JobFilterForm` has no worker variant: the page it filters is manager/admin only
in the view, so there is no field to hide and no scoping to double up on.

### Quick date ranges

Every filter card opens with a row of quick ranges — **Vše**, *posledních 7 dní*,
*posledních 30 dní*, *minulý měsíc* — from `DATE_PRESETS` and
`_date_preset_links(request)` in `workorders/views.py`, rendered by
`templates/workorders/_date_presets.html`.

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

Adding a filtered page means both halves: `'date_presets':
_date_preset_links(request)` in the context *and* the include in the template.

## Time-worked reporting

`workorders/views.py::_time_worked_summary` sums `WorkerHours.hours` per person
over the scoped jobs — the hours each person typed for themselves, not a share
of anything.

One implementation detail that is easy to break: the view re-queries by
`pk__in` into a fresh `scoped` queryset before aggregating. The collaborator
filters join the many-to-many table, and aggregating over a joined queryset
multiplies the `Sum` by the number of matched collaborators.

## What is deliberately absent

- **No stock balances.** See the note at the top; this is the big one.
- **No REST API.** `rest_framework` is installed and configured for session auth,
  but there are no serializers, viewsets, or routes. It is a placeholder.
- **No JavaScript.** `django_htmx` is installed and htmx is loaded in
  `base.html`, but no template uses an `hx-*` attribute. Every page is a plain
  form POST and redirect. `widget_tweaks` is likewise installed and unused.
- **No CSS files.** All styling is one inline `<style>` block in `base.html`.
- **No soft deletes.** Catalog entries are retired with `is_active = False`,
  which removes them from every dropdown while preserving their history.
  Deletion is blocked by `on_delete=PROTECT` once anything references them.
