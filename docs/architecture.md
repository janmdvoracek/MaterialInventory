# Architecture

A modular monolith: one Django project (`config`) with four apps that depend on
each other in one direction only — `accounts` and `materials` hold the reference
data, `inventory` records what happens to it, and `workorders` groups those
records into jobs.

```
accounts ──┐
           ├──> inventory ──> workorders
materials ─┘        (StockMovement.work_order → WorkOrder)
```

---

## The ledger

`inventory.StockMovement` is the heart of the system, and it is **append-only**.

| Field | Meaning |
|---|---|
| `material`, `location` | What, and where. |
| `quantity` | **Signed.** Positive is stock in, negative is stock out. |
| `movement_type` | `RECEIPT`, `SHIPMENT`, `TRANSFORM_CONSUME`, `TRANSFORM_PRODUCE`, `ADJUSTMENT`. |
| `work_order` | Set only for the two `TRANSFORM_*` types. |
| `notes`, `created_at`, `created_by` | Audit trail. |

Current stock is **always derived**:

```python
StockMovement.objects.filter(material=m, location=l).aggregate(Sum('quantity'))
```

There is no `Material.quantity_on_hand` column and there must never be one. A
stored counter can drift from the history that produced it; a derived one
cannot. The cost is a `SUM` per lookup, which is why there's an index on
`(material, location)`. At depot scale this is not a concern.

Consequences worth internalising:

- **Nothing is corrected by editing or deleting.** A mistake is fixed by
  appending a compensating `ADJUSTMENT`, which is why adjustments require a
  written reason.
- **Stock can go negative** — the dashboard renders negative rows in red rather
  than hiding them. This is a signal, usually of a missed receipt.
- **Row count grows forever.** History and exports are paginated (50/page) and
  the export streams with `.iterator()` rather than materialising the queryset.

## Stock sufficiency, and the race it has to survive

Shipments, transform-consumes, and decreasing adjustments must not take more
than exists. The naive version — read the sum, compare, write — is a
check-then-act race: two concurrent shipments can both read "10 available" and
both write −8.

`inventory/services.py::get_available_quantity(material, location, lock=False)`
closes it:

```python
with transaction.atomic():
    available = get_available_quantity(material, location, lock=True)
    if quantity > available:
        ...reject...
    StockMovement.objects.create(...)
```

With `lock=True` it issues `SELECT ... FOR UPDATE` on the **`Material` row**
before summing. Concurrent writers for the same material serialise on that row,
so the second one reads a sum that already includes the first one's write. The
lock is on `Material` rather than on the movement rows because you cannot lock
rows that don't exist yet — the point is to serialise writers, and an arbitrary
shared row is the standard way to do it.

**`lock=True` is only valid inside `transaction.atomic()`.** Outside one, the
lock is released immediately and buys nothing.

`StockLockConcurrencyTests` in `inventory/tests.py` exercises this with real
threads and real concurrent transactions. If you change the locking, verify
those tests by temporarily removing the `select_for_update()` and confirming
they fail — a concurrency test that cannot fail is worthless.

### Two layers, and only one of them counts

The check appears twice, deliberately:

1. `ShipmentForm.clean()` / `AdjustmentForm.clean()` do an **unlocked** sum so
   the user gets a friendly inline field error.
2. The **view** redoes it with `lock=True` inside `transaction.atomic()`.

Only the second is race-safe. The first is a user-experience affordance. **New
code that writes stock must perform the locked check itself** and must never
assume a form already validated.

### Materials that skip the check

`Material.track_stock = False` exempts a material from every sufficiency check.
This is for things the depot consumes but never formally receives — soil
excavated on site, for example. Without the flag, the first attempt to consume
such a material fails against a zero balance forever.

The flag is honoured in all four places that consume stock (shipment form and
view, adjustment form and view, transform view). A new consumption path must
honour it too.

## Work orders

A transformation is one `WorkOrder` plus the movements it caused:

```
WorkOrder #17  "Crushing gravel"
├── StockMovement  TRANSFORM_CONSUME  −20 t  Raw stone   @ Yard
├── StockMovement  TRANSFORM_PRODUCE  +14 t  Gravel 8/16 @ Yard
├── StockMovement  TRANSFORM_PRODUCE  +5 t   Gravel 4/8  @ Yard
├── MachineUsage   Crusher  3.5 h
└── collaborators  [novak, svoboda]
```

The whole submission is one atomic block. Specifically:

- Consumed rows for the **same material and location** are summed across
  formset rows *before* the check, so three rows of 5 t are tested as 15 t, not
  as three independent 5 t checks against the same balance.
- Every shortfall is collected and reported together, rather than failing on
  the first — one round trip tells the worker everything that's wrong.
- A shortfall rolls back machine usage as well. There is no partial job.

### `collaborators`

Other people who worked the job. Its only functional effect is **visibility**: a
collaborator sees the job's movements and hours in their own history, and is
credited its hours. Workers may only pick other workers; managers and admins may
pick anyone. Nobody can pick themselves — the submitter is already the creator.

### `Machine.total_hours`

A plain running counter, not derived from `MachineUsage` rows. It is maintained
by `MachineUsage.save()` and `.delete()` via `F()` expressions, which handle
creation, an hours delta on edit, and reassignment to a different machine.

That logic lives on the **model**, not in the view, so that Django admin inline
edits keep the counter correct too.

> **Therefore: never write `MachineUsage` through `bulk_create()`,
> `queryset.update()`, or `queryset.delete()`.** They bypass the model methods
> and silently desynchronise the counter, with no error.

This is the one place the codebase stores a derived-looking number. Unlike
stock, it is a monotonic operational metric rather than an auditable balance,
and cheap to correct by hand in the admin if it ever drifts.

## Roles and permissions

`accounts.User` extends `AbstractUser` with a `role` field.

| Role | `is_staff` / `is_superuser` | App access |
|---|---|---|
| `WORKER` | no | Receipt, Shipment, Transform; own records only |
| `MANAGER` | no | + Adjustments, + everyone's records |
| `ADMIN` | **yes / yes** | + Django admin |

Two gates:

- `accounts/decorators.py::role_required(*roles)` on the view. Anonymous users
  get the login redirect; authenticated users with the wrong role get a 403 —
  not a bounce back to login, which would be a confusing dead end.
- `User.is_manager_or_admin` for conditional UI and query scoping.

**Superusers bypass both.** `createsuperuser` never sets a `role`, so a
bootstrap admin would otherwise default to `WORKER` and be locked out of the app
it administers.

Managers deliberately get **no** Django admin access. Everything they need is in
the app itself; the admin is a back office for editing the catalog and accounts,
where an accidental edit has no audit trail.

`ADMIN` accounts get `is_staff` **and** `is_superuser`. `is_staff` alone permits
logging in to `/admin/` and then shows an empty "you don't have permission"
index — worse than no access at all. Both `seed_data` and
`CustomUserAdmin.save_model` derive the two flags from `role`, so they stay
consistent however the account was created.

### How worker scoping is enforced

Three views scope their results — movement history, machine-usage history, and
time worked. Each does it **twice**, and the two are not equivalent:

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

## Filter forms

Every list view follows one pattern, and the edge cases are the point:

| Request | Behaviour |
|---|---|
| No querystring at all (`form.is_bound` false) | Show everything in scope. |
| Valid filters | Apply them. |
| **Invalid** filters | Return **`.none()`**. |

The last row is deliberate. Silently ignoring a bad filter would hand back the
entire ledger under a heading that says "these are your filtered results" — the
most dangerous possible response, because it looks like an answer. The template
renders an explicit warning instead.

Note the ordering: `is_bound` is checked *before* `is_valid()`, because an
unbound form is also not valid, and conflating them would return nothing on a
plain page load.

## Time-worked reporting

`workorders/views.py::_time_worked_summary` credits **every participant with the
job's full machine hours**, not a per-person share. If two people jointly ran a
3-hour crushing job, each is credited 3 hours.

So the column deliberately sums to more than `Machine.total_hours` whenever
people collaborate. It measures **labour time**, not machine runtime. Anyone
reconciling the two numbers needs to know this; it is not a bug.

One implementation detail that is easy to break: the view re-queries by
`pk__in` into a fresh `scoped` queryset before aggregating. The collaborator
filters join the many-to-many table, and aggregating over a joined queryset
multiplies the `Sum` by the number of matched collaborators.

## The CSV export

`inventory/views.py::movement_history_export` streams the currently filtered
history. It targets **Czech Excel, not RFC 4180**, and every deviation is
deliberate. See [localization.md](localization.md#the-csv-export) for the four
format decisions and the formula-injection guard.

It respects the same scoping as the history view it mirrors — a worker's export
contains only a worker's rows.

## What is deliberately absent

- **No REST API.** `rest_framework` is installed and configured for session auth,
  but there are no serializers, viewsets, or routes. It is a placeholder.
- **No JavaScript.** `django_htmx` is installed and htmx is loaded in
  `base.html`, but no template uses an `hx-*` attribute. Every page is a plain
  form POST and redirect. `widget_tweaks` is likewise installed and unused.
- **No CSS files.** All styling is one inline `<style>` block in `base.html`.
- **No soft deletes.** Catalog entries are retired with `is_active = False`,
  which removes them from every dropdown while preserving their history.
  Deletion is blocked by `on_delete=PROTECT` once anything references them.
