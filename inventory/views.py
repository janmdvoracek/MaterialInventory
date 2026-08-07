from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import redirect, render

from accounts.decorators import role_required
from accounts.models import User

from .forms import AdjustmentForm, ReceiptForm, ShipmentForm
from .models import StockMovement
from .services import get_available_quantity


@login_required
def dashboard(request):
    stock = (
        StockMovement.objects.values(
            'material__id', 'material__name', 'material__sku', 'material__unit_of_measure', 'location__name'
        )
        .annotate(quantity=Sum('quantity'))
        .filter(quantity__gt=0)
        .order_by('material__name', 'location__name')
    )
    return render(request, 'inventory/dashboard.html', {'stock': stock})


@login_required
def receipt_create(request):
    if request.method == 'POST':
        form = ReceiptForm(request.POST)
        if form.is_valid():
            StockMovement.objects.create(
                material=form.cleaned_data['material'],
                location=form.cleaned_data['location'],
                quantity=form.cleaned_data['quantity'],
                movement_type=StockMovement.MovementType.RECEIPT,
                notes=form.cleaned_data['notes'],
                created_by=request.user,
            )
            messages.success(request, 'Receipt recorded.')
            return redirect('dashboard')
    else:
        form = ReceiptForm()
    return render(request, 'inventory/receipt_form.html', {'form': form})


@login_required
def shipment_create(request):
    if request.method == 'POST':
        form = ShipmentForm(request.POST)
        if form.is_valid():
            material = form.cleaned_data['material']
            location = form.cleaned_data['location']
            quantity = form.cleaned_data['quantity']
            with transaction.atomic():
                available = get_available_quantity(material, location, lock=True)
                if quantity > available:
                    messages.error(
                        request,
                        f'Only {available} {material.unit_of_measure} of {material} available at {location}; '
                        'shipment aborted.',
                    )
                else:
                    StockMovement.objects.create(
                        material=material,
                        location=location,
                        quantity=-quantity,
                        movement_type=StockMovement.MovementType.SHIPMENT,
                        notes=form.cleaned_data['notes'],
                        created_by=request.user,
                    )
                    messages.success(request, 'Shipment recorded.')
                    return redirect('dashboard')
    else:
        form = ShipmentForm()
    return render(request, 'inventory/shipment_form.html', {'form': form})


@role_required(User.Role.MANAGER, User.Role.ADMIN)
def adjustment_create(request):
    if request.method == 'POST':
        form = AdjustmentForm(request.POST)
        if form.is_valid():
            material = form.cleaned_data['material']
            location = form.cleaned_data['location']
            quantity = form.cleaned_data['quantity']
            if form.cleaned_data['direction'] == 'DECREASE':
                quantity = -quantity
            with transaction.atomic():
                if quantity < 0:
                    available = get_available_quantity(material, location, lock=True)
                    if -quantity > available:
                        messages.error(
                            request,
                            f'Only {available} {material.unit_of_measure} of {material} available at {location}; '
                            'adjustment aborted.',
                        )
                        return render(request, 'inventory/adjustment_form.html', {'form': form})
                StockMovement.objects.create(
                    material=material,
                    location=location,
                    quantity=quantity,
                    movement_type=StockMovement.MovementType.ADJUSTMENT,
                    notes=form.cleaned_data['notes'],
                    created_by=request.user,
                )
            messages.success(request, 'Adjustment recorded.')
            return redirect('dashboard')
    else:
        form = AdjustmentForm()
    return render(request, 'inventory/adjustment_form.html', {'form': form})
