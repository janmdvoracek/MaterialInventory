from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.shortcuts import redirect, render

from inventory.models import StockMovement
from inventory.services import get_available_quantity
from materials.models import Machine

from .forms import ConsumedFormSet, MachineUsageFormSet, ProducedFormSet, WorkOrderForm
from .models import MachineUsage, WorkOrder


@login_required
def transform_create(request):
    if request.method == 'POST':
        order_form = WorkOrderForm(request.POST)
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
                            MachineUsage.objects.create(
                                work_order=work_order,
                                machine=row['machine'],
                                hours=row['hours'],
                            )
                            Machine.objects.filter(pk=row['machine'].pk).update(
                                total_hours=F('total_hours') + row['hours']
                            )
                        messages.success(request, 'Zpracování bylo zaznamenáno.')
                        return redirect('dashboard')
    else:
        order_form = WorkOrderForm()
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
