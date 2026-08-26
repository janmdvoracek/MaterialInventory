import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.decorators import role_required
from accounts.models import User

from .forms import AdjustmentForm, HistoryFilterForm, ReceiptForm, ShipmentForm
from .models import StockMovement
from .services import get_available_quantity

HISTORY_PAGE_SIZE = 50


@login_required
def dashboard(request):
    stock = (
        StockMovement.objects.values(
            'material__id',
            'material__name',
            'material__sku',
            'material__unit_of_measure',
            'location__id',
            'location__name',
        )
        .annotate(quantity=Sum('quantity'))
        .exclude(quantity=0)
        .order_by('material__name', 'location__name')
    )
    return render(request, 'inventory/dashboard.html', {'stock': stock})


def _filtered_movements(request):
    form = HistoryFilterForm(request.GET or None, user=request.user)
    movements = StockMovement.objects.select_related('material', 'location', 'created_by', 'work_order')
    if not request.user.is_manager_or_admin:
        # Workers only ever see their own movements, plus transformations they
        # collaborated on (added as a WorkOrder collaborator by whoever
        # submitted it); enforced here (not just by hiding the `created_by`
        # filter field) so it can't be bypassed via the querystring directly.
        movements = movements.filter(Q(created_by=request.user) | Q(work_order__collaborators=request.user)).distinct()
    if not form.is_bound:
        # No filters submitted at all (initial page load) — show everything.
        return form, movements
    if not form.is_valid():
        # A filter was submitted but is unusable. Return nothing rather than
        # silently ignoring it, which would hand back the whole ledger and read
        # as "these are your filtered results".
        return form, movements.none()
    data = form.cleaned_data
    if data.get('material'):
        movements = movements.filter(material=data['material'])
    if data.get('location'):
        movements = movements.filter(location=data['location'])
    if data.get('movement_type'):
        movements = movements.filter(movement_type=data['movement_type'])
    if data.get('created_by'):
        movements = movements.filter(created_by=data['created_by'])
    if data.get('date_from'):
        movements = movements.filter(created_at__date__gte=data['date_from'])
    if data.get('date_to'):
        movements = movements.filter(created_at__date__lte=data['date_to'])
    return form, movements


@login_required
def movement_history(request):
    form, movements = _filtered_movements(request)
    page_obj = Paginator(movements, HISTORY_PAGE_SIZE).get_page(request.GET.get('page'))
    querystring = request.GET.copy()
    querystring.pop('page', None)
    return render(
        request,
        'inventory/movement_history.html',
        {'form': form, 'page_obj': page_obj, 'querystring': querystring.urlencode()},
    )


# The export is opened in Excel by depot managers on Czech Windows, which means
# three things: it splits rows on the Windows list separator (`;`, not `,`), it
# expects a comma decimal separator, and it only recognises UTF-8 when the file
# opens with a BOM — without one it assumes windows-1250 and mangles every
# diacritic in the material names.
CSV_DELIMITER = ';'
CSV_BOM = '﻿'


class _Echo:
    def write(self, value):
        return value


def _csv_number(value):
    """Decimal -> Czech numeric literal, e.g. Decimal('-4.000') -> '-4,000'.

    Deliberately not routed through _csv_safe: a leading apostrophe would stop
    Excel treating the cell as a number, which matters for the negative
    quantities that make up every shipment row.
    """
    return str(value).replace('.', ',')


def _csv_safe(value):
    """Neutralise spreadsheet formula injection.

    A cell starting with =, +, - or @ is executed as a formula by Excel and
    LibreOffice, so free-text fields (notes, names) could otherwise run code on
    whoever opens the export. Prefixing an apostrophe forces it to stay text.
    """
    text = str(value)
    if text.startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + text
    return text


@login_required
def movement_history_export(request):
    _, movements = _filtered_movements(request)
    writer = csv.writer(_Echo(), delimiter=CSV_DELIMITER)

    def rows():
        yield CSV_BOM
        yield writer.writerow(
            ['Datum', 'SKU', 'Materiál', 'Lokalita', 'Typ', 'Množství', 'Jednotka', 'Zakázka', 'Vytvořil', 'Poznámka']
        )
        for movement in movements.iterator(chunk_size=2000):
            yield writer.writerow(
                [
                    # localtime() so the export matches the timestamps shown in
                    # the history table; the raw value is UTC, TIME_ZONE is not.
                    # Czech date order, so Excel parses it as a date rather than
                    # leaving it as text.
                    timezone.localtime(movement.created_at).strftime('%d.%m.%Y %H:%M:%S'),
                    _csv_safe(movement.material.sku),
                    _csv_safe(movement.material.name),
                    _csv_safe(movement.location.name),
                    _csv_safe(movement.get_movement_type_display()),
                    _csv_number(movement.quantity),
                    _csv_safe(movement.material.unit_of_measure),
                    movement.work_order_id or '',
                    _csv_safe(movement.created_by.username),
                    _csv_safe(movement.notes),
                ]
            )

    response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="movement_history.csv"'
    return response


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
                created_at=form.cleaned_data['occurred_at'] or timezone.now(),
            )
            messages.success(request, 'Příjem byl zaznamenán.')
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
                if material.track_stock:
                    available = get_available_quantity(material, location, lock=True)
                else:
                    available = None
                if available is not None and quantity > available:
                    messages.error(
                        request,
                        f'K dispozici je pouze {available} {material.unit_of_measure} materiálu {material} '
                        f'na lokalitě {location}; výdej byl zrušen.',
                    )
                else:
                    StockMovement.objects.create(
                        material=material,
                        location=location,
                        quantity=-quantity,
                        movement_type=StockMovement.MovementType.SHIPMENT,
                        notes=form.cleaned_data['notes'],
                        created_by=request.user,
                        created_at=form.cleaned_data['occurred_at'] or timezone.now(),
                    )
                    messages.success(request, 'Výdej byl zaznamenán.')
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
                if quantity < 0 and material.track_stock:
                    available = get_available_quantity(material, location, lock=True)
                    if -quantity > available:
                        messages.error(
                            request,
                            f'K dispozici je pouze {available} {material.unit_of_measure} materiálu {material} '
                            f'na lokalitě {location}; úprava byla zrušena.',
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
            messages.success(request, 'Úprava byla zaznamenána.')
            return redirect('dashboard')
    else:
        form = AdjustmentForm()
    return render(request, 'inventory/adjustment_form.html', {'form': form})
