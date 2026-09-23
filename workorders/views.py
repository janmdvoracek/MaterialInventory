"""Recording a job (Zpracování) and reviewing it (Přehled and the job pages)."""

from decimal import Decimal
from typing import NamedTuple

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import localize
from django.views.decorators.http import require_POST

from accounts.decorators import REVIEWER_ROLES, role_required

from .forms import JOB_SECTIONS, JobFilterForm, WorkOrderForm, job_row_formset
from .models import MachineRefuel, MachineUsage, StockMovement, WorkerHours, WorkOrder
from .reports import _list_page_context


class JobRows(NamedTuple):
    """Everything one submission records, split the way the tables are.

    `usages` and `refuels` both come off the machines section: a row carrying
    motohodiny is a `MachineUsage`, a row carrying litres is a `MachineRefuel`,
    and one row can be both or either (see `MachineUsageForm.check_row`).
    """

    consumed: list
    produced: list
    usages: list
    refuels: list
    worker_hours: dict


def _collect_rows(formsets):
    """The filled-in rows of each section, as typed. Duplicates were already refused."""

    def filled(prefix, field):
        return [form.cleaned_data for form in formsets[prefix] if form.cleaned_data.get(field)]

    machine_rows = filled('machines', 'machine')
    return JobRows(
        consumed=filled('consumed', 'material'),
        produced=filled('produced', 'material'),
        usages=[row for row in machine_rows if row.get('hours') is not None],
        refuels=[row for row in machine_rows if row.get('litres') is not None],
        worker_hours={row['user']: row['hours'] for row in filled('workers', 'user')},
    )


def _is_fuel_only(rows):
    """True when the submission records nothing but fill-ups.

    Such a job has no consumed and produced totals to balance, nobody's hours to
    record and no work to label, so the mass balance and the two otherwise
    required job fields are not asked of it. It is still an ordinary
    `WorkOrder`: dated, reviewed and listed like any other.
    """
    return bool(rows.refuels) and not (rows.consumed or rows.produced or rows.usages or rows.worker_hours)


# Required on every job except a fuel-only one; see `WorkOrderForm`.
WORK_FIELDS = ('location', 'description', 'hours')


def _require_work_fields(order_form):
    """Put Django's own "required" complaint on any of `WORK_FIELDS` left empty.

    Reuses each field's own message rather than a hardcoded Czech string, so it
    comes from the same catalog as every other required field on the page.
    """
    for name in WORK_FIELDS:
        if order_form.cleaned_data.get(name) in (None, ''):
            order_form.add_error(name, order_form.fields[name].error_messages['required'])


def _balance_error(consumed_rows, produced_rows):
    """The Czech error if consumed and produced totals are not exactly equal, else None."""
    if not consumed_rows or not produced_rows:
        return 'Přidejte alespoň jednu položku spotřeby a jednu položku výroby.'
    consumed_total = sum((row['quantity'] for row in consumed_rows), Decimal('0'))
    produced_total = sum((row['quantity'] for row in produced_rows), Decimal('0'))
    if consumed_total != produced_total:
        # localize() for the decimal comma the rest of the UI shows.
        return (
            'Celkové množství spotřeby a výroby se musí rovnat '
            f'(spotřeba {localize(consumed_total)}, výroba {localize(produced_total)}).'
        )
    return None


def _write_job_rows(work_order, author, own_hours, rows):
    """Replace the job's hours, line items, machine usage and fill-ups with the submitted rows.

    Call inside `transaction.atomic()`. The rows belong to `author`, not the
    editor. `own_hours` is None on a fuel-only job, which nobody claimed hours
    for, and then the author gets no `WorkerHours` row at all.
    """
    # Derived from the hours rows, so the two can't disagree.
    work_order.collaborators.set(rows.worker_hours.keys())
    work_order.worker_hours.all().delete()
    hours_rows = list(rows.worker_hours.items())
    if own_hours is not None:
        hours_rows.insert(0, (author, own_hours))
    WorkerHours.objects.bulk_create(
        WorkerHours(work_order=work_order, user=user, hours=hours) for user, hours in hours_rows
    )

    work_order.movements.all().delete()
    # Consumed quantities are stored negative, so a material's net is a plain Sum.
    StockMovement.objects.bulk_create(
        StockMovement(
            work_order=work_order,
            created_by=author,
            material=row['material'],
            quantity=sign * row['quantity'],
            movement_type=movement_type,
        )
        for section_rows, sign, movement_type in (
            (rows.consumed, -1, StockMovement.MovementType.TRANSFORM_CONSUME),
            (rows.produced, 1, StockMovement.MovementType.TRANSFORM_PRODUCE),
        )
        for row in section_rows
    )

    work_order.machine_usages.all().delete()
    MachineUsage.objects.bulk_create(
        MachineUsage(work_order=work_order, machine=row['machine'], hours=row['hours'], tons=row['tons'])
        for row in rows.usages
    )

    # Its own table, written from the same rows: a machine that was only
    # refuelled has a fill-up here and nothing above.
    work_order.machine_refuels.all().delete()
    MachineRefuel.objects.bulk_create(
        MachineRefuel(work_order=work_order, machine=row['machine'], litres=row['litres']) for row in rows.refuels
    )


# Only the pressed submit button is posted, so its name being present is the signal.
ADD_ROW_BUTTONS = {f'add_{section.prefix}': section.prefix for section in JOB_SECTIONS}
REMOVE_ROW_BUTTONS = {f'remove_{section.prefix}': section.prefix for section in JOB_SECTIONS}

# Applied to every section on every rebuild, so no POST can leave one empty.
MIN_ROWS_PER_SECTION = 1

# A rebuild reads TOTAL_FORMS straight from the POST, so cap it. Matches MAX_NUM_FORMS.
MAX_ROWS_PER_SECTION = 1000


def _pressed_row_button(post_data):
    """`(prefix, +1 or -1)` for a pressed row button, or None for a submission."""
    for name, prefix in ADD_ROW_BUTTONS.items():
        if name in post_data:
            return prefix, 1
    for name, prefix in REMOVE_ROW_BUTTONS.items():
        if name in post_data:
            return prefix, -1
    return None


def _submitted_rows(post_data, section):
    """One section's rows as the raw POST strings, for the unbound rebuild."""
    prefix = section.prefix
    try:
        count = int(post_data.get(f'{prefix}-TOTAL_FORMS', 0))
    except (TypeError, ValueError):
        count = 0
    return [
        {name: post_data.get(f'{prefix}-{index}-{name}', '') for name in section.row_form.base_fields}
        for index in range(min(count, MAX_ROWS_PER_SECTION))
    ]


def _submitted_author(post_data, submitter):
    """Who „Zapsat za" names in this POST, else the submitter.

    Needed before the formsets are built, since the author shapes the
    collaborator list. Errors are ignored here; the page's own form reports them.
    """
    form = WorkOrderForm(post_data, user=submitter)
    form.is_valid()
    return form.author_or(submitter)


def _resized_section(rows, delta):
    """Apply a row button: one blank row more, or the last row less.

    Returns `(rows, blank_rows)`, never fewer than `MIN_ROWS_PER_SECTION` in total.
    """
    if delta > 0:
        blank_rows = 1
    else:
        if delta < 0 and len(rows) > MIN_ROWS_PER_SECTION:
            rows = rows[:-1]
        blank_rows = 0
    return rows, max(blank_rows, MIN_ROWS_PER_SECTION - len(rows))


def _section_form_kwargs(prefix, *, author, viewer, keep=None):
    """Row-form kwargs: the author/viewer pair for workers, `keep` for the catalog sections."""
    if prefix == 'workers':
        return {'user': author, 'viewer': viewer}
    return {'keep': (keep or {}).get(prefix, ())}


def _job_forms(request, *, author, viewer, order_form_user=None, keep=None, initial=None):
    """Every form on the job page. Returns `(order_form, formsets_by_prefix, submitted)`.

    - Row button: rebuilt *unbound* from the POST with that section resized, so
      a half-filled form comes back without errors.
    - Any other POST: bound, and `submitted` is True.
    - GET: fresh forms, pre-filled from `initial` (keyed `order` and by section prefix).

    `keep` is keyed by section prefix, plus `location` for the job form's own picker.
    """
    order_kwargs = {'user': order_form_user, 'keep_location': (keep or {}).get('location', ())}
    form_kwargs = {
        section.prefix: _section_form_kwargs(section.prefix, author=author, viewer=viewer, keep=keep)
        for section in JOB_SECTIONS
    }
    pressed = _pressed_row_button(request.POST) if request.method == 'POST' else None
    if pressed:
        pressed_prefix, delta = pressed
        order_form = WorkOrderForm(**order_kwargs)
        # Read off the form's own fields, so „Zapsat za" is carried when present.
        order_form.initial = {name: request.POST.get(name, '') for name in order_form.fields}
        formsets = {}
        for section in JOB_SECTIONS:
            rows, blank_rows = _resized_section(
                _submitted_rows(request.POST, section),
                delta if section.prefix == pressed_prefix else 0,
            )
            formsets[section.prefix] = job_row_formset(
                section, rows=rows, blank_rows=blank_rows, form_kwargs=form_kwargs[section.prefix]
            )
        return order_form, formsets, False
    if request.method == 'POST':
        order_form = WorkOrderForm(request.POST, **order_kwargs)
        formsets = {
            section.prefix: job_row_formset(section, data=request.POST, form_kwargs=form_kwargs[section.prefix])
            for section in JOB_SECTIONS
        }
        return order_form, formsets, True
    initial = initial or {}
    order_form = WorkOrderForm(initial=initial.get('order'), **order_kwargs)
    formsets = {
        section.prefix: job_row_formset(
            section, rows=initial.get(section.prefix, ()), form_kwargs=form_kwargs[section.prefix]
        )
        for section in JOB_SECTIONS
    }
    return order_form, formsets, False


def _valid_job_rows(request, order_form, formsets):
    """The submitted rows, or None if any form is invalid or the job doesn't balance.

    A fuel-only job skips both the mass balance and `WORK_FIELDS`; anything that
    records work is held to both. The balance error has no field, so it goes on
    `messages`.
    """
    if not (order_form.is_valid() and all(formset.is_valid() for formset in formsets.values())):
        return None
    rows = _collect_rows(formsets)
    if _is_fuel_only(rows):
        return rows
    _require_work_fields(order_form)
    balance_error = _balance_error(rows.consumed, rows.produced)
    if balance_error:
        messages.error(request, balance_error)
    # add_error() has already marked the form invalid, so its errors render.
    return None if balance_error or order_form.errors else rows


def _job_context(order_form, formsets, **extra):
    """The job page's context: the job form, `sections` in render order, and each formset by name.

    The two row bounds go out as well, so `job_rows.js` reads the floor and the
    cap off the page instead of restating them.
    """
    return {
        'order_form': order_form,
        'sections': [(section, formsets[section.prefix]) for section in JOB_SECTIONS],
        'min_rows': MIN_ROWS_PER_SECTION,
        'max_rows': MAX_ROWS_PER_SECTION,
        **{section.context_name: formsets[section.prefix] for section in JOB_SECTIONS},
        **extra,
    }


@login_required
def transform_create(request):
    # The author shapes the collaborator list, so settle it before any formset.
    author = _submitted_author(request.POST, request.user) if request.method == 'POST' else request.user
    order_form, formsets, submitted = _job_forms(
        request, author=author, viewer=request.user, order_form_user=request.user
    )
    if submitted:
        rows = _valid_job_rows(request, order_form, formsets)
        if rows is not None:
            # The submitter decides: a manager's job, even one typed for a
            # worker, is approved at once; a worker's waits for review.
            approved = request.user.is_manager_or_admin
            with transaction.atomic():
                work_order = WorkOrder.objects.create(
                    created_by=author,
                    location=order_form.cleaned_data['location'],
                    description=order_form.cleaned_data['description'],
                    notes=order_form.cleaned_data['notes'],
                    performed_on=order_form.cleaned_data['performed_on'],
                    status=WorkOrder.Status.APPROVED if approved else WorkOrder.Status.PENDING,
                    reviewed_at=timezone.now() if approved else None,
                    reviewed_by=request.user if approved else None,
                )
                _write_job_rows(work_order, author, order_form.cleaned_data['hours'], rows)
            messages.success(
                request,
                'Zpracování bylo zaznamenáno.'
                if approved
                else 'Zpracování bylo zaznamenáno a čeká na schválení vedoucím.',
            )
            return redirect('transform_create')
    return render(request, 'workorders/transform_form.html', _job_context(order_form, formsets))


def _trim(value):
    """A stored Decimal without trailing zeros, so the stricter form accepts it as `initial`.

    Avoids normalize() turning 100.00 into 1E+2.
    """
    normalized = value.normalize()
    return normalized.quantize(Decimal(1)) if normalized.as_tuple().exponent > 0 else normalized


@role_required(*REVIEWER_ROLES)
def job_dashboard(request):
    """Every recorded job, newest first. Manager-only, so not scoped per worker."""
    form = JobFilterForm(request.GET or None)
    jobs = (
        WorkOrder.objects.select_related('created_by', 'reviewed_by', 'location')
        .annotate(total_hours=Sum('worker_hours__hours'))
        .prefetch_related('worker_hours__user')
        .order_by('-performed_on', '-created_at')
    )
    jobs = form.filter(jobs)
    return render(
        request,
        'workorders/job_dashboard.html',
        {
            'form': form,
            'pending_count': WorkOrder.objects.filter(status=WorkOrder.Status.PENDING).count(),
            **_list_page_context(request, jobs),
        },
    )


def _job_line_items(work_order):
    """The job's consumed and produced rows, quantities as they were typed."""
    consumed, produced = [], []
    for movement in work_order.movements.select_related('material'):
        movement.typed_quantity = abs(movement.quantity)
        if movement.movement_type == StockMovement.MovementType.TRANSFORM_CONSUME:
            consumed.append(movement)
        else:
            produced.append(movement)
    return consumed, produced


@role_required(*REVIEWER_ROLES)
def job_detail(request, pk):
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by', 'reviewed_by', 'location'), pk=pk)
    consumed, produced = _job_line_items(work_order)
    return render(
        request,
        'workorders/job_detail.html',
        {
            'work_order': work_order,
            'consumed': consumed,
            'produced': produced,
            'machine_usages': work_order.machine_usages.select_related('machine'),
            'machine_refuels': work_order.machine_refuels.select_related('machine'),
            'worker_hours': work_order.worker_hours.select_related('user'),
        },
    )


@role_required(*REVIEWER_ROLES)
def job_edit(request, pk):
    """Correct a recorded job. Does not approve it."""
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by'), pk=pk)
    # The rows stay the author's, not the editing manager's.
    author = work_order.created_by
    consumed, produced = _job_line_items(work_order)
    usages = list(work_order.machine_usages.all())
    refuels = list(work_order.machine_refuels.all())
    # Keep retired records the job names selectable; see `_offer_recorded`. A
    # machine the job only refuelled counts, or its fill-up would be dropped.
    keep = {
        'consumed': [movement.material_id for movement in consumed],
        'produced': [movement.material_id for movement in produced],
        'machines': [usage.machine_id for usage in usages] + [refuel.machine_id for refuel in refuels],
        'location': [work_order.location_id] if work_order.location_id else [],
    }
    order_form, formsets, submitted = _job_forms(
        request,
        author=author,
        viewer=request.user,
        keep=keep,
        initial=_edit_initial(work_order, author, consumed, produced, usages, refuels)
        if request.method == 'GET'
        else None,
    )
    if submitted:
        rows = _valid_job_rows(request, order_form, formsets)
        if rows is not None:
            with transaction.atomic():
                work_order.location = order_form.cleaned_data['location']
                work_order.description = order_form.cleaned_data['description']
                work_order.notes = order_form.cleaned_data['notes']
                work_order.performed_on = order_form.cleaned_data['performed_on']
                work_order.save(update_fields=['location', 'description', 'notes', 'performed_on'])
                _write_job_rows(work_order, author, order_form.cleaned_data['hours'], rows)
            messages.success(request, 'Zpracování bylo upraveno.')
            return redirect('job_detail', pk=work_order.pk)
    # Set on every path, since an invalid POST re-renders too.
    order_form.fields['hours'].label = f'Hodiny – {author.username}'
    return render(request, 'workorders/job_edit.html', _job_context(order_form, formsets, work_order=work_order))


def _machine_section_rows(usages, refuels):
    """The machines section as the form takes it: one row per machine, runtime and fuel merged.

    The two live in separate tables and either can be there without the other —
    a fuel-only job has fill-ups and no usage — while the section refuses a
    machine twice, so they are zipped back into one row each. Fill-ups are
    summed for the same reason the duplicate message says to: only an admin edit
    can put one machine on two of them, and one row is all there is to render.
    """
    rows = {}
    for usage in usages:
        rows[usage.machine_id] = {'machine': usage.machine_id, 'hours': usage.hours, 'tons': usage.tons}
    for refuel in refuels:
        row = rows.setdefault(refuel.machine_id, {'machine': refuel.machine_id})
        row['litres'] = (row.get('litres') or Decimal('0')) + refuel.litres
    # Trimmed here rather than per field, since which of them a row carries varies.
    return [
        {key: _trim(value) if isinstance(value, Decimal) else value for key, value in row.items()}
        for row in rows.values()
    ]


def _edit_initial(work_order, author, consumed, produced, usages, refuels):
    """`job_edit`'s pre-fill: the job as recorded, quantities as they were typed."""
    own_hours = work_order.worker_hours.filter(user=author).first()
    return {
        'order': {
            'location': work_order.location_id,
            'description': work_order.description,
            'notes': work_order.notes,
            'hours': _trim(own_hours.hours) if own_hours else None,
            'performed_on': work_order.performed_on,
        },
        'consumed': [{'material': m.material_id, 'quantity': _trim(m.typed_quantity)} for m in consumed],
        'produced': [{'material': m.material_id, 'quantity': _trim(m.typed_quantity)} for m in produced],
        'machines': _machine_section_rows(usages, refuels),
        'workers': [
            {'user': row.user_id, 'hours': _trim(row.hours)} for row in work_order.worker_hours.exclude(user=author)
        ],
    }


@role_required(*REVIEWER_ROLES)
@require_POST
def job_approve(request, pk):
    work_order = get_object_or_404(WorkOrder, pk=pk)
    work_order.status = WorkOrder.Status.APPROVED
    work_order.reviewed_at = timezone.now()
    work_order.reviewed_by = request.user
    work_order.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])
    messages.success(request, 'Zpracování bylo schváleno.')
    return redirect('job_detail', pk=work_order.pk)


@role_required(*REVIEWER_ROLES)
def job_delete(request, pk):
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by'), pk=pk)
    if request.method == 'POST':
        # Every row on the job cascades.
        work_order.delete()
        messages.success(request, 'Zpracování bylo smazáno.')
        return redirect('job_dashboard')
    consumed, produced = _job_line_items(work_order)
    return render(
        request,
        'workorders/job_confirm_delete.html',
        {
            'work_order': work_order,
            'consumed': consumed,
            'produced': produced,
            'machine_usages': work_order.machine_usages.select_related('machine'),
            'machine_refuels': work_order.machine_refuels.select_related('machine'),
        },
    )
