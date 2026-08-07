from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import redirect, render

from .forms import ReceiptForm, ShipmentForm
from .models import StockMovement


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
            StockMovement.objects.create(
                material=form.cleaned_data['material'],
                location=form.cleaned_data['location'],
                quantity=-form.cleaned_data['quantity'],
                movement_type=StockMovement.MovementType.SHIPMENT,
                notes=form.cleaned_data['notes'],
                created_by=request.user,
            )
            messages.success(request, 'Shipment recorded.')
            return redirect('dashboard')
    else:
        form = ShipmentForm()
    return render(request, 'inventory/shipment_form.html', {'form': form})
