from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from inventory.models import StockMovement
from inventory.services import get_available_quantity

from .forms import ConsumedFormSet, ProducedFormSet, WorkOrderForm
from .models import WorkOrder


@login_required
def transform_create(request):
    if request.method == 'POST':
        order_form = WorkOrderForm(request.POST)
        consumed_formset = ConsumedFormSet(request.POST, prefix='consumed')
        produced_formset = ProducedFormSet(request.POST, prefix='produced')
        if order_form.is_valid() and consumed_formset.is_valid() and produced_formset.is_valid():
            consumed_rows = [f.cleaned_data for f in consumed_formset if f.cleaned_data.get('material')]
            produced_rows = [f.cleaned_data for f in produced_formset if f.cleaned_data.get('material')]
            if not consumed_rows and not produced_rows:
                messages.error(request, 'Add at least one consumed or produced item.')
            else:
                requested = defaultdict(Decimal)
                for row in consumed_rows:
                    requested[(row['material'], row['location'])] += row['quantity']

                with transaction.atomic():
                    shortfalls = []
                    for (material, location), quantity in requested.items():
                        available = get_available_quantity(material, location, lock=True)
                        if quantity > available:
                            shortfalls.append(
                                f'Only {available} {material.unit_of_measure} of {material} available at '
                                f'{location} (requested {quantity}).'
                            )
                    if shortfalls:
                        for shortfall in shortfalls:
                            messages.error(request, shortfall)
                    else:
                        work_order = WorkOrder.objects.create(
                            created_by=request.user,
                            description=order_form.cleaned_data['description'],
                        )
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
                        messages.success(request, 'Transformation recorded.')
                        return redirect('dashboard')
    else:
        order_form = WorkOrderForm()
        consumed_formset = ConsumedFormSet(prefix='consumed')
        produced_formset = ProducedFormSet(prefix='produced')
    return render(
        request,
        'workorders/transform_form.html',
        {
            'order_form': order_form,
            'consumed_formset': consumed_formset,
            'produced_formset': produced_formset,
        },
    )
