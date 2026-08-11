from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render

from inventory.models import StockMovement
from inventory.services import get_available_quantity
from materials.models import Machine

from accounts.models import User

from .forms import (
    ConsumedFormSet,
    MachineHistoryFilterForm,
    MachineUsageFormSet,
    ProducedFormSet,
    TimeWorkedFilterForm,
    WorkOrderForm,
)
from .models import MachineUsage, WorkOrder

HISTORY_PAGE_SIZE = 50


@login_required
def transform_create(request):
    if request.method == 'POST':
        order_form = WorkOrderForm(request.POST, user=request.user)
        consumed_formset = ConsumedFormSet(request.POST, prefix='consumed')
        produced_formset = ProducedFormSet(request.POST, prefix='produced')
        machine_formset = MachineUsageFormSet(request.POST, prefix='machines')
        if (
            order_form.is_valid()
            and consumed_formset.is_valid()
            and produced_formset.is_valid()
            and machine_formset.is_valid()
        ):
            consumed_rows = [f.cleaned_data for f in consumed_formset if f.cleaned_data.get('material')]
            produced_rows = [f.cleaned_data for f in produced_formset if f.cleaned_data.get('material')]
            machine_rows = [f.cleaned_data for f in machine_formset if f.cleaned_data.get('machine')]
            if not consumed_rows and not produced_rows:
                messages.error(request, 'Přidejte alespoň jednu položku spotřeby nebo výroby.')
            else:
                requested = defaultdict(Decimal)
                for row in consumed_rows:
                    requested[(row['material'], row['location'])] += row['quantity']

                with transaction.atomic():
                    shortfalls = []
                    for (material, location), quantity in requested.items():
                        if not material.track_stock:
                            continue
                        available = get_available_quantity(material, location, lock=True)
                        if quantity > available:
                            shortfalls.append(
                                f'K dispozici je pouze {available} {material.unit_of_measure} materiálu {material} '
                                f'na lokalitě {location} (požadováno {quantity}).'
                            )
                    if shortfalls:
                        for shortfall in shortfalls:
                            messages.error(request, shortfall)
                    else:
                        work_order = WorkOrder.objects.create(
                            created_by=request.user,
                            description=order_form.cleaned_data['description'],
                        )
                        work_order.collaborators.set(order_form.cleaned_data['collaborators'])
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
                        return redirect('dashboard')
    else:
        order_form = WorkOrderForm(user=request.user)
        consumed_formset = ConsumedFormSet(prefix='consumed')
        produced_formset = ProducedFormSet(prefix='produced')
        machine_formset = MachineUsageFormSet(prefix='machines')
    return render(
        request,
        'workorders/transform_form.html',
        {
            'order_form': order_form,
            'consumed_formset': consumed_formset,
            'produced_formset': produced_formset,
            'machine_formset': machine_formset,
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

    Each participant is credited with the job's *full* machine hours, not a
    share of them: if two workers jointly ran a 3-hour crushing job, each of
    them spent 3 hours on it. That means the column totals more than
    `Machine.total_hours` whenever people collaborate — it measures labour
    time, not machine runtime.
    """
    totals = {}

    def add(user_id, hours, orders):
        entry = totals.setdefault(user_id, {'hours': Decimal('0'), 'orders': 0})
        entry['hours'] += hours or Decimal('0')
        entry['orders'] += orders

    created = work_orders.values('created_by').annotate(
        hours=Sum('machine_usages__hours'), orders=Count('id', distinct=True)
    )
    for row in created:
        add(row['created_by'], row['hours'], row['orders'])

    collaborated = (
        work_orders.filter(collaborators__isnull=False)
        .values('collaborators')
        .annotate(hours=Sum('machine_usages__hours'), orders=Count('id', distinct=True))
    )
    for row in collaborated:
        add(row['collaborators'], row['hours'], row['orders'])

    users = User.objects.in_bulk(totals.keys())
    rows = [
        {'user': users[user_id], 'hours': data['hours'], 'orders': data['orders']}
        for user_id, data in totals.items()
        if user_id in users
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
        scoped.annotate(total_hours=Sum('machine_usages__hours'))
        .select_related('created_by')
        .prefetch_related('collaborators', 'machine_usages__machine')
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
