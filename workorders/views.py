from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.utils.formats import localize

from accounts.models import User
from inventory.models import StockMovement
from materials.models import Machine

from .forms import (
    ConsumedFormSet,
    MachineHistoryFilterForm,
    MachineUsageFormSet,
    ProducedFormSet,
    TimeWorkedFilterForm,
    WorkerHoursFormSet,
    WorkOrderForm,
)
from .models import MachineUsage, WorkerHours, WorkOrder

HISTORY_PAGE_SIZE = 50


@login_required
def transform_create(request):
    if request.method == 'POST':
        order_form = WorkOrderForm(request.POST)
        consumed_formset = ConsumedFormSet(request.POST, prefix='consumed')
        produced_formset = ProducedFormSet(request.POST, prefix='produced')
        machine_formset = MachineUsageFormSet(request.POST, prefix='machines')
        worker_formset = WorkerHoursFormSet(request.POST, prefix='workers', form_kwargs={'user': request.user})
        if (
            order_form.is_valid()
            and consumed_formset.is_valid()
            and produced_formset.is_valid()
            and machine_formset.is_valid()
            and worker_formset.is_valid()
        ):
            consumed_rows = [f.cleaned_data for f in consumed_formset if f.cleaned_data.get('material')]
            produced_rows = [f.cleaned_data for f in produced_formset if f.cleaned_data.get('material')]
            machine_rows = [f.cleaned_data for f in machine_formset if f.cleaned_data.get('machine')]
            # Same combining rule as the consumed rows: naming a person twice
            # adds their hours up rather than failing the unique constraint.
            worker_hours = defaultdict(Decimal)
            for form in worker_formset:
                if form.cleaned_data.get('user'):
                    worker_hours[form.cleaned_data['user']] += form.cleaned_data['hours']
            # Mass balance: a transformation moves material between fractions,
            # it does not create or destroy it, so the two sides have to add up.
            # Compared as totals, not row by row — one input is normally crushed
            # into several output fractions. Exact Decimal equality; `quantity`
            # carries 3 decimal places and Decimal('5.0') == Decimal('5'), so
            # trailing zeros don't matter.
            consumed_total = sum((row['quantity'] for row in consumed_rows), Decimal('0'))
            produced_total = sum((row['quantity'] for row in produced_rows), Decimal('0'))
            if not consumed_rows or not produced_rows:
                messages.error(request, 'Přidejte alespoň jednu položku spotřeby a jednu položku výroby.')
            elif consumed_total != produced_total:
                messages.error(
                    request,
                    'Celkové množství spotřeby a výroby se musí rovnat '
                    f'(spotřeba {localize(consumed_total)}, výroba {localize(produced_total)}).',
                )
            else:
                # Still one transaction: a job's line items, hours and machine
                # usage are a single record and must not land half-written.
                # Nothing inside here can fail a business rule: the mass-balance
                # check above is about this one job's own rows, not a stock
                # level, so it needs no database state and runs before the write.
                with transaction.atomic():
                    work_order = WorkOrder.objects.create(
                        created_by=request.user,
                        description=order_form.cleaned_data['description'],
                    )
                    # Collaborators are derived from the hours rows, so the two
                    # can't disagree about who worked the job.
                    work_order.collaborators.set(worker_hours.keys())
                    WorkerHours.objects.create(
                        work_order=work_order, user=request.user, hours=order_form.cleaned_data['hours']
                    )
                    for user, hours in worker_hours.items():
                        WorkerHours.objects.create(work_order=work_order, user=user, hours=hours)
                    for row in consumed_rows:
                        StockMovement.objects.create(
                            material=row['material'],
                            location=row['location'],
                            quantity=-row['quantity'],
                            movement_type=StockMovement.MovementType.TRANSFORM_CONSUME,
                            work_order=work_order,
                            created_by=request.user,
                        )
                    for row in produced_rows:
                        StockMovement.objects.create(
                            material=row['material'],
                            location=row['location'],
                            quantity=row['quantity'],
                            movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE,
                            work_order=work_order,
                            created_by=request.user,
                        )
                    for row in machine_rows:
                        # MachineUsage.save() keeps Machine.total_hours in sync.
                        MachineUsage.objects.create(
                            work_order=work_order,
                            machine=row['machine'],
                            hours=row['hours'],
                        )
                messages.success(request, 'Zpracování bylo zaznamenáno.')
                return redirect('transform_create')
    else:
        order_form = WorkOrderForm()
        consumed_formset = ConsumedFormSet(prefix='consumed')
        produced_formset = ProducedFormSet(prefix='produced')
        machine_formset = MachineUsageFormSet(prefix='machines')
        worker_formset = WorkerHoursFormSet(prefix='workers', form_kwargs={'user': request.user})
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
    machines = Machine.objects.filter(is_active=True).order_by('name')
    return render(request, 'workorders/machine_dashboard.html', {'machines': machines})


def _filtered_machine_usages(request):
    form = MachineHistoryFilterForm(request.GET or None, user=request.user)
    usages = MachineUsage.objects.select_related('machine', 'work_order', 'work_order__created_by')
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
        usages = usages.filter(created_at__date__gte=data['date_from'])
    if data.get('date_to'):
        usages = usages.filter(created_at__date__lte=data['date_to'])
    return form, usages


@login_required
def machine_usage_history(request):
    form, usages = _filtered_machine_usages(request)
    page_obj = Paginator(usages, HISTORY_PAGE_SIZE).get_page(request.GET.get('page'))
    querystring = request.GET.copy()
    querystring.pop('page', None)
    return render(
        request,
        'workorders/machine_usage_history.html',
        {'form': form, 'page_obj': page_obj, 'querystring': querystring.urlencode()},
    )


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
    work_orders = WorkOrder.objects.all()
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
                work_orders = work_orders.filter(created_at__date__gte=data['date_from'])
            if data.get('date_to'):
                work_orders = work_orders.filter(created_at__date__lte=data['date_to'])

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
        .order_by('-created_at')
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
        },
    )
