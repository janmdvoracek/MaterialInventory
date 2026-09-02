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
| `work_order` | The job this line belongs to. Nullable only because pre-removal rows had no job. |
| `created_at` | Defaults to now. Overridable, but nothing overrides it today. |
| `recorded_at` | When the row was written. `auto_now_add`, so it cannot be set. |
| `notes`, `created_by` | Audit trail. |

Rows written before the removal may still carry raw `RECEIPT`, `SHIPMENT` or
`ADJUSTMENT` values. Those are no longer members of `MovementType`, so
`get_movement_type_display()` returns the bare string for them. They were left in
the database on purpose — deleting them was not part of the change.

`created_at` stays `default=timezone.now` rather than `auto_now_add` so that a
job could be back-dated later without a schema change. The back-dating checkbox
that used to set it (*„Jiné datum a čas než teď"*) lived on the Příjem and Výdej
forms and went with them, so in practice `created_at` always equals
`recorded_at` right now.

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
  (so it excludes them and not the manager), and rewritten `StockMovement` rows
  keep `created_by = author`.
- `_write_job_rows` and `job_delete` delete `MachineUsage` **one instance at a
  time**. See `Machine.total_hours` below — a cascade or a queryset delete would
  leave a machine carrying the hours of a job that no longer exists.

### `WorkerHours` vs `MachineUsage`

Two unrelated numbers. Do not derive one from the other.

- **`WorkerHours`** is labour: what a person typed for themselves. The
  submitter's own hours come from `WorkOrderForm.hours` (*„Moje hodiny"*, and it
  is required); each collaborator's come from a `WorkerHoursFormSet` row. A
  `UniqueConstraint` allows one row per person per job, and naming the same
  person on two rows sums their hours rather than tripping it.
- **`MachineUsage.hours`** is motohodiny — machine runtime.

The Hodiny report totals `WorkerHours` only, so it has no fixed relationship to
`Machine.total_hours`. That is not a reconciliation bug.

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

`collaborator_queryset` decides who may be named — never yourself (you are
already the creator), and a plain worker may only name other workers.

The one place the two can drift is the Django admin: `WorkOrderAdmin` exposes
the `collaborators` multi-select and the `WorkerHours` inline as independent
widgets, so an admin edit can leave a collaborator with no hours row, or a
person with hours who is not a collaborator.

### `Machine.total_hours`

A plain running counter, not derived from `MachineUsage` rows. It is maintained
by `MachineUsage.save()` and `.delete()` via `F()` expressions, which handle
creation, an hours delta on edit, and reassignment to a different machine.

That logic lives on the **model**, not in the view, so that Django admin inline
edits keep the counter correct too.

> **Therefore: never write `MachineUsage` through `bulk_create()`,
> `queryset.update()`, or `queryset.delete()`.** They bypass the model methods
> and silently desynchronise the counter, with no error. A **cascading delete
> counts as one of those**: deleting a `WorkOrder` does not call
> `MachineUsage.delete()`, which is why `job_delete` walks the rows itself.

**The Stroje page does not read this counter.** The counter is bumped the moment
a row is written, so it includes jobs still waiting for approval;
`machine_dashboard` instead annotates `Sum('usages__hours')` filtered to
approved jobs, so all three read-only pages report the same scope. The counter
stays as it is — the admin shows it, and it is still what `MachineUsage` keeps
correct — so the two numbers legitimately differ while a job is pending.

## Pages and URLs

All in `workorders`, all mounted at the **root** by `config/urls.py` —
`inventory` contributes no URLs at all.

| URL | Name | What |
|---|---|---|
| `/` | `transform_create` | Zpracování — the form, and the landing page |
| `/hours/` | `time_worked` | Hodiny |
| `/machines/` | `machine_dashboard` | Stroje |
| `/machines/history/` | `machine_usage_history` | Machine usage log |
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
| `MANAGER` | no | + Přehled (approve/edit/return/delete), + Stroje in the nav, + everyone's records |
| `ADMIN` | **yes / yes** | + Django admin |

Two gates:

- `accounts/decorators.py::role_required(*roles)` on the view. Anonymous users
  get the login redirect; authenticated users with the wrong role get a 403 —
  not a bounce back to login, which would be a confusing dead end.
- `User.is_manager_or_admin` for conditional UI and query scoping.

**The `/jobs/` review views all use `role_required`** — a worker who types one of
those URLs gets a 403, not a page. `machine_dashboard` and
`machine_usage_history` still do not: they are hidden from a worker's nav but
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
