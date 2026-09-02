from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import localize
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User
from inventory.models import StockMovement
from materials.models import Machine

from .forms import (
    ConsumedFormSet,
    JobFilterForm,
    MachineFilterForm,
    MachineUsageFormSet,
    ProducedFormSet,
    TimeWorkedFilterForm,
    WorkerHoursFormSet,
    WorkOrderForm,
)
from .models import MachineUsage, WorkerHours, WorkOrder

HISTORY_PAGE_SIZE = 50
# How many of their own recent jobs a worker sees at the top of Hodiny. Short on
# purpose: it is a status check, not a history — the full one is the manager's
# Přehled.
MY_JOBS_LIMIT = 5
# Reviewing a job is a manager/admin job. Gated on the view, not just by hiding
# the nav entry — a hidden link is not access control.
REVIEWER_ROLES = (User.Role.MANAGER, User.Role.ADMIN)


def _last_month(today):
    """The previous calendar month, first day to last."""
    last_day = today.replace(day=1) - timedelta(days=1)
    return last_day.replace(day=1), last_day


# Quick ranges offered above every filter form. Each entry maps today's date to
# the (from, to) pair the links should set — `None` clears that end of the
# range, which is what makes "Vše" the way back out of a range on a phone, where
# emptying a date input by hand is fiddly. The spans include today, so
# "posledních 7 dní" is today plus the six before it.
DATE_PRESETS = (
    ('all', 'Vše', lambda today: (None, None)),
    ('7d', 'Posledních 7 dní', lambda today: (today - timedelta(days=6), today)),
    ('30d', 'Posledních 30 dní', lambda today: (today - timedelta(days=29), today)),
    ('last_month', 'Minulý měsíc', _last_month),
)


def _collect_rows(consumed_formset, produced_formset, machine_formset, worker_formset):
    """The filled-in rows of a submitted job, one collection per section.

    Rows are taken exactly as typed — repeated material rows stay separate line
    items. Only the worker rows are combined: naming the same person twice adds
    their hours up rather than tripping `unique_worker_hours_per_work_order`.
    """
    consumed_rows = [f.cleaned_data for f in consumed_formset if f.cleaned_data.get('material')]
    produced_rows = [f.cleaned_data for f in produced_formset if f.cleaned_data.get('material')]
    machine_rows = [f.cleaned_data for f in machine_formset if f.cleaned_data.get('machine')]
    worker_hours = defaultdict(Decimal)
    for form in worker_formset:
        if form.cleaned_data.get('user'):
            worker_hours[form.cleaned_data['user']] += form.cleaned_data['hours']
    return consumed_rows, produced_rows, machine_rows, worker_hours


def _balance_error(consumed_rows, produced_rows):
    """The Czech complaint about a job that doesn't add up, or None.

    Mass balance: a transformation moves material between fractions, it does not
    create or destroy it, so the two sides have to add up. Compared as totals,
    not row by row — one input is normally crushed into several output
    fractions. Exact Decimal equality; the form accepts 2 decimal places and
    Decimal('5.0') == Decimal('5'), so trailing zeros don't matter. There is no
    database state behind this, which is why it runs before the write rather
    than in a model `clean()`.
    """
    if not consumed_rows or not produced_rows:
        return 'Přidejte alespoň jednu položku spotřeby a jednu položku výroby.'
    consumed_total = sum((row['quantity'] for row in consumed_rows), Decimal('0'))
    produced_total = sum((row['quantity'] for row in produced_rows), Decimal('0'))
    if consumed_total != produced_total:
        # localize() and not an f-string: the rest of the UI shows 9,5 and this
        # message would otherwise print 9.5.
        return (
            'Celkové množství spotřeby a výroby se musí rovnat '
            f'(spotřeba {localize(consumed_total)}, výroba {localize(produced_total)}).'
        )
    return None


def _write_job_rows(work_order, author, own_hours, consumed_rows, produced_rows, machine_rows, worker_hours):
    """Make the job's line items, hours and machine usage match what was typed.

    Used by both the worker's own submission and a manager's later edit, so it
    clears what is there before writing. The caller must already be inside a
    `transaction.atomic()`: the three record types are one job and must not land
    half-written. `author` — not the person doing the editing — owns the rows.
    """
    # Collaborators are derived from the hours rows, so the two can't disagree
    # about who worked the job.
    work_order.collaborators.set(worker_hours.keys())
    work_order.worker_hours.all().delete()
    WorkerHours.objects.create(work_order=work_order, user=author, hours=own_hours)
    for user, hours in worker_hours.items():
        WorkerHours.objects.create(work_order=work_order, user=user, hours=hours)

    work_order.movements.all().delete()
    for row in consumed_rows:
        StockMovement.objects.create(
            material=row['material'],
            quantity=-row['quantity'],
            movement_type=StockMovement.MovementType.TRANSFORM_CONSUME,
            work_order=work_order,
            created_by=author,
        )
    for row in produced_rows:
        StockMovement.objects.create(
            material=row['material'],
            quantity=row['quantity'],
            movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE,
            work_order=work_order,
            created_by=author,
        )

    # One instance at a time, never queryset.delete(): MachineUsage.delete() is
    # what keeps Machine.total_hours in sync, and a bulk delete skips it.
    for usage in work_order.machine_usages.all():
        usage.delete()
    for row in machine_rows:
        # MachineUsage.save() keeps Machine.total_hours in sync.
        MachineUsage.objects.create(
            work_order=work_order,
            machine=row['machine'],
            hours=row['hours'],
            tons=row['tons'],
        )


@login_required
def transform_create(request):
    if request.method == 'POST':
        order_form = WorkOrderForm(request.POST, user=request.user)
        consumed_formset = ConsumedFormSet(request.POST, prefix='consumed')
        produced_formset = ProducedFormSet(request.POST, prefix='produced')
        machine_formset = MachineUsageFormSet(request.POST, prefix='machines')
        # Who the job belongs to is chosen on this same form, and it decides who
        # may be named as a collaborator — you cannot collaborate with yourself.
        # So the author has to be settled before the hours rows are built. A
        # form that does not validate has no author; the submitter stands in,
        # only so the invalid page can be re-rendered.
        author = order_form.author_or(request.user) if order_form.is_valid() else request.user
        worker_formset = WorkerHoursFormSet(
            request.POST, prefix='workers', form_kwargs={'user': author, 'viewer': request.user}
        )
        if (
            order_form.is_valid()
            and consumed_formset.is_valid()
            and produced_formset.is_valid()
            and machine_formset.is_valid()
            and worker_formset.is_valid()
        ):
            consumed_rows, produced_rows, machine_rows, worker_hours = _collect_rows(
                consumed_formset, produced_formset, machine_formset, worker_formset
            )
            error = _balance_error(consumed_rows, produced_rows)
            if error:
                messages.error(request, error)
            else:
                # A worker's job is a proposal until a manager signs it off, so
                # it stays out of the Hodiny/Stroje reports until then. A
                # manager has nobody above them to approve it, so theirs counts
                # straight away — and that goes for one they typed on a worker's
                # behalf too: they are the reviewer, and they just saw the work
                # written down. The *submitter* decides this, not the author.
                approved = request.user.is_manager_or_admin
                with transaction.atomic():
                    work_order = WorkOrder.objects.create(
                        created_by=author,
                        description=order_form.cleaned_data['description'],
                        performed_on=order_form.cleaned_data['performed_on'],
                        status=WorkOrder.Status.APPROVED if approved else WorkOrder.Status.PENDING,
                        reviewed_at=timezone.now() if approved else None,
                        reviewed_by=request.user if approved else None,
                    )
                    _write_job_rows(
                        work_order,
                        author,
                        order_form.cleaned_data['hours'],
                        consumed_rows,
                        produced_rows,
                        machine_rows,
                        worker_hours,
                    )
                messages.success(
                    request,
                    'Zpracování bylo zaznamenáno.'
                    if approved
                    else 'Zpracování bylo zaznamenáno a čeká na schválení vedoucím.',
                )
                return redirect('transform_create')
    else:
        order_form = WorkOrderForm(user=request.user)
        consumed_formset = ConsumedFormSet(prefix='consumed')
        produced_formset = ProducedFormSet(prefix='produced')
        machine_formset = MachineUsageFormSet(prefix='machines')
        worker_formset = WorkerHoursFormSet(
            prefix='workers', form_kwargs={'user': request.user, 'viewer': request.user}
        )
    return render(
        request,
        'workorders/transform_form.html',
        {
            'order_form': order_form,
            'consumed_formset': consumed_formset,
            'produced_formset': produced_formset,
            'machine_formset': machine_formset,
            'worker_formset': worker_formset,
        },
    )


@login_required
def machine_dashboard(request):
    """Stroje: the filter, per-machine totals under it, the usage rows below.

    Laid out like Hodiny, and for the same reason — the totals and the rows are
    the same data at two zoom levels, so one filter drives both and you can read
    a number and then see what it is made of without changing page.
    """
    form, usages = _filtered_machine_usages(request)
    machines = _machine_summary(usages, form)
    page_obj = Paginator(usages, HISTORY_PAGE_SIZE).get_page(request.GET.get('page'))
    querystring = request.GET.copy()
    querystring.pop('page', None)
    return render(
        request,
        'workorders/machine_dashboard.html',
        {
            'form': form,
            'machines': machines,
            'page_obj': page_obj,
            'querystring': querystring.urlencode(),
            'date_presets': _date_preset_links(request),
        },
    )


def _machine_summary(usages, form):
    """Hours and tonnage per machine over `usages`.

    Totalled from the usage rows rather than read off `Machine.total_hours`:
    that counter is bumped the moment a row is written, so it also holds hours
    from jobs still waiting for approval, and it knows nothing about the filter.
    The counter stays as it is — the admin shows it, and `MachineUsage` keeps it
    correct — so the two legitimately differ while a job is pending.

    Every active machine is listed, not just the ones with rows in range: with
    no filter that is the full fleet, and under a date filter a machine sitting
    at 0 h is the answer to "what ran last week". Naming a machine in the filter
    narrows the list to it, since the rest would be a column of zeros nobody
    asked for.

    `tons` is nullable — rows written before the column existed mean *unknown*,
    not zero — and `Sum` skips NULLs, so a machine with no recorded tonnage sums
    to `None` and renders as a dash. `0 t` would claim it processed nothing.
    """
    if form.is_bound and not form.is_valid():
        # Same rule as the rows below: an unusable filter shows nothing, rather
        # than a fleet of zeros under a "these are your filtered results" head.
        return Machine.objects.none()
    machines = Machine.objects.filter(is_active=True)
    if form.is_bound and form.cleaned_data.get('machine'):
        machines = machines.filter(pk=form.cleaned_data['machine'].pk)
    in_scope = Q(usages__in=usages.values('pk'))
    return machines.annotate(
        filtered_hours=Sum('usages__hours', filter=in_scope),
        filtered_tons=Sum('usages__tons', filter=in_scope),
    ).order_by('name')


def _filtered_machine_usages(request):
    form = MachineFilterForm(request.GET or None, user=request.user)
    # Unapproved jobs are proposals, not evidence — they stay out of the ledger
    # until a manager signs them off.
    # Dated by the job, not by the row: `MachineUsage.created_at` is when the
    # row was written, and `job_edit` rewrites every row, so a corrected job
    # would otherwise drift to the day it was corrected.
    usages = (
        MachineUsage.objects.filter(work_order__status=WorkOrder.Status.APPROVED)
        .select_related('machine', 'work_order', 'work_order__created_by')
        .order_by('-work_order__performed_on', '-created_at')
    )
    if not request.user.is_manager_or_admin:
        # Workers only ever see their own machine usage, plus usage from
        # transformations they collaborated on; enforced here (not just by
        # hiding the `created_by` filter field) so it can't be bypassed via
        # the querystring directly.
        usages = usages.filter(
            Q(work_order__created_by=request.user) | Q(work_order__collaborators=request.user)
        ).distinct()
    if not form.is_bound:
        # No filters submitted at all (initial page load) — show everything.
        return form, usages
    if not form.is_valid():
        # A filter was submitted but is unusable. Return nothing rather than
        # silently ignoring it, which would hand back the whole ledger and read
        # as "these are your filtered results".
        return form, usages.none()
    data = form.cleaned_data
    if data.get('machine'):
        usages = usages.filter(machine=data['machine'])
    if data.get('created_by'):
        usages = usages.filter(work_order__created_by=data['created_by'])
    if data.get('date_from'):
        usages = usages.filter(work_order__performed_on__gte=data['date_from'])
    if data.get('date_to'):
        usages = usages.filter(work_order__performed_on__lte=data['date_to'])
    return form, usages


def _date_preset_links(request):
    """The quick-range links for a filter form, one per `DATE_PRESETS` entry.

    Plain links rather than a form field: one tap on a phone, no JS, and the
    range they set stays visible in the two date inputs afterwards because a
    bound form re-renders whatever the querystring holds. Dates go in as
    ISO — Django appends `%Y-%m-%d` to every locale's `DATE_INPUT_FORMATS`, so
    `cs` parses them, and `<input type="date">` only accepts that shape anyway.

    Every other filter value is carried over, so picking a range does not drop
    the machine or worker already chosen; `page` is dropped, because a new range
    starts at page one. `active` marks the link whose range is the one currently
    in effect, which makes the row a read-out as well as a control.
    """
    today = timezone.localdate()
    current = (request.GET.get('date_from', ''), request.GET.get('date_to', ''))
    links = []
    for key, label, span in DATE_PRESETS:
        params = request.GET.copy()
        params.pop('page', None)
        value = []
        for field, day in zip(('date_from', 'date_to'), span(today)):
            if day is None:
                params.pop(field, None)
                value.append('')
            else:
                params[field] = day.isoformat()
                value.append(day.isoformat())
        links.append(
            {
                'key': key,
                'label': label,
                'querystring': params.urlencode(),
                'active': current == tuple(value),
            }
        )
    return links


def _participation_filter(user):
    """A user "worked on" a job if they submitted it or were named a collaborator."""
    return Q(created_by=user) | Q(collaborators=user)


def _time_worked_summary(work_orders):
    """Hours per person across `work_orders`.

    These are the labour hours each person typed on the Transform form, not a
    share of the job's machine runtime — two people on a 3-hour crushing job
    each report what they personally worked, so this column has no fixed
    relationship to `Machine.total_hours`.
    """
    totals = (
        WorkerHours.objects.filter(work_order__in=work_orders)
        .values('user')
        .annotate(hours=Sum('hours'), orders=Count('work_order', distinct=True))
    )
    users = User.objects.in_bulk([row['user'] for row in totals])
    rows = [
        {'user': users[row['user']], 'hours': row['hours'], 'orders': row['orders']}
        for row in totals
        if row['user'] in users
    ]
    rows.sort(key=lambda row: (-row['hours'], row['user'].username))
    return rows


@login_required
def time_worked(request):
    form = TimeWorkedFilterForm(request.GET or None, user=request.user)
    # Approved jobs only: hours a manager has not signed off yet are not
    # reportable, so they do not show up here for anyone, not even their author.
    work_orders = WorkOrder.objects.filter(status=WorkOrder.Status.APPROVED)
    if not request.user.is_manager_or_admin:
        # Workers only ever see jobs they took part in; enforced here (not just
        # by hiding the `worker` filter field) so it can't be bypassed via the
        # querystring directly.
        work_orders = work_orders.filter(_participation_filter(request.user))
    if form.is_bound:
        if not form.is_valid():
            # An unusable filter must not fall through to showing everything.
            work_orders = work_orders.none()
        else:
            data = form.cleaned_data
            if data.get('worker'):
                work_orders = work_orders.filter(_participation_filter(data['worker']))
            if data.get('date_from'):
                work_orders = work_orders.filter(performed_on__gte=data['date_from'])
            if data.get('date_to'):
                work_orders = work_orders.filter(performed_on__lte=data['date_to'])

    # Re-query by pk so the aggregation below joins cleanly — the collaborator
    # filters above already join the M2M, which would otherwise skew the sums.
    scoped = WorkOrder.objects.filter(pk__in=work_orders.values('pk'))

    summary = _time_worked_summary(scoped)
    if not request.user.is_manager_or_admin:
        # A collaborated job was created by someone else, so it would otherwise
        # put that person's total on a worker's screen.
        summary = [row for row in summary if row['user'] == request.user]

    detail = (
        scoped.annotate(total_hours=Sum('worker_hours__hours'))
        .select_related('created_by')
        .prefetch_related('collaborators', 'worker_hours__user')
        .order_by('-performed_on', '-created_at')
    )
    page_obj = Paginator(detail, HISTORY_PAGE_SIZE).get_page(request.GET.get('page'))
    querystring = request.GET.copy()
    querystring.pop('page', None)
    return render(
        request,
        'workorders/time_worked.html',
        {
            'form': form,
            'summary': summary,
            'total_hours': sum((row['hours'] for row in summary), Decimal('0')),
            'page_obj': page_obj,
            'querystring': querystring.urlencode(),
            'date_presets': _date_preset_links(request),
            'my_jobs': _my_recent_jobs(request.user),
        },
    )


def _my_recent_jobs(user):
    """A worker's own last few submissions, with the status of each.

    This is the only feedback a worker gets about the review: a job is never
    handed back to its author, and everything above filters to APPROVED, so
    without it there is no telling a job still waiting for approval from one
    that never landed. It sits on Hodiny because that is where a worker goes to
    see their hours, and a pending job is precisely the hours that are missing
    from the summary above.

    Submissions only, not jobs they were named on — a collaborator did not
    record the job, and their hours from it appear in the summary once it is
    approved. Managers get nothing here: Hodiny is the whole depot's report for
    them, and Přehled already lists every job with its status.
    """
    if user.is_manager_or_admin:
        return WorkOrder.objects.none()
    return (
        WorkOrder.objects.filter(created_by=user)
        .annotate(my_hours=Sum('worker_hours__hours', filter=Q(worker_hours__user=user)))
        # The one list still ordered by when it was typed, not when the work
        # happened: it is a recency list of *submissions*, and a job someone
        # just back-dated to last month has to appear at the top of it anyway —
        # checking on it is the whole reason the list exists. The date column
        # still shows `performed_on`, like everywhere else.
        .order_by('-created_at')[:MY_JOBS_LIMIT]
    )


def _trim(value):
    """A stored quantity as the entry form would have accepted it.

    The models keep more decimal places than the form allows (quantity: 3 vs 2)
    and the hours fields are stored with 2 places but typed with 1, so feeding a
    raw value back as `initial` renders `5.000` and the edit form then rejects
    its own prefill. Trailing zeros are what makes the difference, so drop them
    — without letting normalize() turn 100.00 into 1E+2.
    """
    normalized = value.normalize()
    return normalized.quantize(Decimal(1)) if normalized.as_tuple().exponent > 0 else normalized


@role_required(*REVIEWER_ROLES)
def job_dashboard(request):
    """Every recorded job, newest first, with the ones awaiting review flagged.

    No worker scoping here, unlike the other list views — the whole page is
    manager/admin only.
    """
    form = JobFilterForm(request.GET or None)
    jobs = (
        WorkOrder.objects.select_related('created_by', 'reviewed_by')
        .annotate(total_hours=Sum('worker_hours__hours'))
        .prefetch_related('worker_hours__user')
        .order_by('-performed_on', '-created_at')
    )
    if form.is_bound:
        if not form.is_valid():
            # Same rule as the other lists: an unusable filter shows nothing
            # rather than quietly handing back everything.
            jobs = jobs.none()
        else:
            data = form.cleaned_data
            if data.get('created_by'):
                jobs = jobs.filter(created_by=data['created_by'])
            if data.get('status'):
                jobs = jobs.filter(status=data['status'])
            if data.get('date_from'):
                jobs = jobs.filter(performed_on__gte=data['date_from'])
            if data.get('date_to'):
                jobs = jobs.filter(performed_on__lte=data['date_to'])
    page_obj = Paginator(jobs, HISTORY_PAGE_SIZE).get_page(request.GET.get('page'))
    querystring = request.GET.copy()
    querystring.pop('page', None)
    return render(
        request,
        'workorders/job_dashboard.html',
        {
            'form': form,
            'page_obj': page_obj,
            'querystring': querystring.urlencode(),
            'date_presets': _date_preset_links(request),
            'pending_count': WorkOrder.objects.filter(status=WorkOrder.Status.PENDING).count(),
        },
    )


def _job_line_items(work_order):
    """The job's consumed and produced rows, quantities as they were typed."""
    consumed, produced = [], []
    for movement in work_order.movements.select_related('material'):
        # Consumed quantities are stored negative; the form asked for a
        # positive number and that is what the reviewer should see.
        movement.typed_quantity = abs(movement.quantity)
        if movement.movement_type == StockMovement.MovementType.TRANSFORM_CONSUME:
            consumed.append(movement)
        else:
            produced.append(movement)
    return consumed, produced


@role_required(*REVIEWER_ROLES)
def job_detail(request, pk):
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by', 'reviewed_by'), pk=pk)
    consumed, produced = _job_line_items(work_order)
    return render(
        request,
        'workorders/job_detail.html',
        {
            'work_order': work_order,
            'consumed': consumed,
            'produced': produced,
            'machine_usages': work_order.machine_usages.select_related('machine'),
            'worker_hours': work_order.worker_hours.select_related('user'),
        },
    )


@role_required(*REVIEWER_ROLES)
def job_edit(request, pk):
    """Correct a recorded job. Approving it stays a separate step — a manager
    can fix a job and still leave the sign-off to someone else."""
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by'), pk=pk)
    # The rows belong to whoever recorded the job, not to the manager editing
    # it: the "moje hodiny" field is the author's, and the collaborator list has
    # to exclude the author rather than the editor.
    author = work_order.created_by
    if request.method == 'POST':
        order_form = WorkOrderForm(request.POST)
        consumed_formset = ConsumedFormSet(request.POST, prefix='consumed')
        produced_formset = ProducedFormSet(request.POST, prefix='produced')
        machine_formset = MachineUsageFormSet(request.POST, prefix='machines')
        worker_formset = WorkerHoursFormSet(
            request.POST, prefix='workers', form_kwargs={'user': author, 'viewer': request.user}
        )
        if (
            order_form.is_valid()
            and consumed_formset.is_valid()
            and produced_formset.is_valid()
            and machine_formset.is_valid()
            and worker_formset.is_valid()
        ):
            consumed_rows, produced_rows, machine_rows, worker_hours = _collect_rows(
                consumed_formset, produced_formset, machine_formset, worker_formset
            )
            error = _balance_error(consumed_rows, produced_rows)
            if error:
                messages.error(request, error)
            else:
                with transaction.atomic():
                    work_order.description = order_form.cleaned_data['description']
                    work_order.performed_on = order_form.cleaned_data['performed_on']
                    work_order.save(update_fields=['description', 'performed_on'])
                    _write_job_rows(
                        work_order,
                        author,
                        order_form.cleaned_data['hours'],
                        consumed_rows,
                        produced_rows,
                        machine_rows,
                        worker_hours,
                    )
                messages.success(request, 'Zpracování bylo upraveno.')
                return redirect('job_detail', pk=work_order.pk)
    else:
        consumed, produced = _job_line_items(work_order)
        own_hours = work_order.worker_hours.filter(user=author).first()
        order_form = WorkOrderForm(
            initial={
                'description': work_order.description,
                'hours': _trim(own_hours.hours) if own_hours else None,
                # Pre-ticked whenever the job was not done the day it was typed
                # in, so a correction does not quietly reset the date to today.
                'use_custom_date': work_order.performed_on != timezone.localtime(work_order.created_at).date(),
                'performed_on': work_order.performed_on,
            }
        )
        consumed_formset = ConsumedFormSet(
            prefix='consumed',
            initial=[
                {
                    'material': movement.material_id,
                    'quantity': _trim(movement.typed_quantity),
                }
                for movement in consumed
            ],
        )
        produced_formset = ProducedFormSet(
            prefix='produced',
            initial=[
                {
                    'material': movement.material_id,
                    'quantity': _trim(movement.typed_quantity),
                }
                for movement in produced
            ],
        )
        machine_formset = MachineUsageFormSet(
            prefix='machines',
            initial=[
                {
                    'machine': usage.machine_id,
                    'hours': _trim(usage.hours),
                    'tons': _trim(usage.tons) if usage.tons is not None else None,
                }
                for usage in work_order.machine_usages.all()
            ],
        )
        worker_formset = WorkerHoursFormSet(
            prefix='workers',
            form_kwargs={'user': author, 'viewer': request.user},
            initial=[
                {'user': row.user_id, 'hours': _trim(row.hours)} for row in work_order.worker_hours.exclude(user=author)
            ],
        )
    return render(
        request,
        'workorders/job_edit.html',
        {
            'work_order': work_order,
            'order_form': order_form,
            'consumed_formset': consumed_formset,
            'produced_formset': produced_formset,
            'machine_formset': machine_formset,
            'worker_formset': worker_formset,
        },
    )


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
        with transaction.atomic():
            # Machine usage one row at a time, and before the cascade could get
            # to it: a cascading delete does not call MachineUsage.delete(), so
            # Machine.total_hours would keep the hours of a job that no longer
            # exists. The line items have to go first as well — their FK to the
            # job is PROTECT, so the job cannot be deleted while they point at
            # it.
            for usage in work_order.machine_usages.all():
                usage.delete()
            work_order.movements.all().delete()
            work_order.worker_hours.all().delete()
            work_order.collaborators.clear()
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
        },
    )
