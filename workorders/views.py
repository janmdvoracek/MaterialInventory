from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render

from inventory.models import StockMovement
from inventory.services import get_available_quantity
from materials.models import Machine

from .forms import ConsumedFormSet, MachineHistoryFilterForm, MachineUsageFormSet, ProducedFormSet, WorkOrderForm
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
