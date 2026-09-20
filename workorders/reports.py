"""The report pages (Hodiny, Stroje, Materiál), their CSV exports, and the
list-page helpers Přehled shares with them."""

import csv
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import Abs, Coalesce
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.formats import number_format

from accounts.decorators import REVIEWER_ROLES, role_required
from accounts.models import User
from machines.models import Machine
from materials.models import Material

from .forms import MachineFilterForm, MaterialFilterForm, TimeWorkedFilterForm, participation_filter
from .models import MachineUsage, StockMovement, WorkerHours, WorkOrder

HISTORY_PAGE_SIZE = 50
# A worker's own recent jobs on Hodiny: a status check, not a history.
MY_JOBS_LIMIT = 5


def _last_month(today):
    """The previous calendar month, first day to last."""
    last_day = today.replace(day=1) - timedelta(days=1)
    return last_day.replace(day=1), last_day


# Quick date ranges: (key, label, today -> (from, to)). None clears that end;
# the spans include today.
DATE_PRESETS = (
    ('all', 'Vše', lambda today: (None, None)),
    ('7d', 'Posledních 7 dní', lambda today: (today - timedelta(days=6), today)),
    ('30d', 'Posledních 30 dní', lambda today: (today - timedelta(days=29), today)),
    ('last_month', 'Minulý měsíc', _last_month),
)


@role_required(*REVIEWER_ROLES)
def machine_dashboard(request):
    """Stroje: the filter, per-machine totals, and the usage rows behind them."""
    form, usages = _filtered_machine_usages(request)
    machines = _machine_summary(usages, form)
    return render(
        request,
        'workorders/machine_dashboard.html',
        {
            'form': form,
            'machines': machines,
            **_list_page_context(request, usages),
        },
    )


def _machine_costs(machine):
    """Set the three cost columns on one summary row, and return it.

    An unset rate means unpriced (None), not free; zero hours with a rate is a
    real 0. „Celkem" sums the priced sides and is None if any of them is
    unknown, rather than understating the bill.
    """
    hours_cost = None
    if machine.hourly_rate is not None:
        hours_cost = (machine.filtered_hours or Decimal('0')) * machine.hourly_rate
    tons_cost = None
    if machine.rate_per_ton is not None and machine.filtered_tons is not None:
        tons_cost = machine.filtered_tons * machine.rate_per_ton
    # Keyed off the rates: an unset rate drops its side, while a rate with
    # unknown tonnage keeps the side and makes the total unknown.
    priced_sides = [
        cost
        for rate, cost in ((machine.hourly_rate, hours_cost), (machine.rate_per_ton, tons_cost))
        if rate is not None
    ]
    machine.filtered_hours_cost = hours_cost
    machine.filtered_tons_cost = tons_cost
    machine.filtered_total_cost = sum(priced_sides, Decimal('0')) if priced_sides and None not in priced_sides else None
    return machine


def _machine_summary(usages, form):
    """Hours, tonnage and cost per machine over `usages`, as a list.

    Every active machine is listed (0 h is an answer), or just the filtered one.
    Tons summing to None means unknown, not zero.
    """
    if form.is_bound and not form.is_valid():
        return []
    machines = Machine.objects.filter(is_active=True)
    if form.is_bound and form.cleaned_data.get('machine'):
        machines = machines.filter(pk=form.cleaned_data['machine'].pk)
    in_scope = Q(usages__in=usages.values('pk'))
    return [
        _machine_costs(machine)
        for machine in machines.annotate(
            filtered_hours=Sum('usages__hours', filter=in_scope),
            filtered_tons=Sum('usages__tons', filter=in_scope),
        ).order_by('name')
    ]


def _filtered_machine_usages(request):
    # Approved jobs only. No per-user scoping: the page is manager-only.
    form = MachineFilterForm(request.GET or None)
    usages = (
        MachineUsage.objects.filter(work_order__status=WorkOrder.Status.APPROVED)
        .select_related('machine', 'work_order', 'work_order__created_by')
        .order_by('-work_order__performed_on', '-id')
    )
    return form, form.filter(usages)


@role_required(*REVIEWER_ROLES)
def material_dashboard(request):
    """Materiál: the filter, per-material tonnage, and the line items behind it."""
    form, movements = _filtered_material_movements(request)
    return render(
        request,
        'workorders/material_dashboard.html',
        {
            'form': form,
            'materials': _material_summary(movements, form),
            **_list_page_context(request, movements),
        },
    )


def _material_summary(movements, form):
    """Consumed, produced and net tonnage per active material over `movements`.

    Quantities are stored signed, so the net is a plain Sum. Zeros here are
    real zeros, unlike tons on Stroje.
    """
    if form.is_bound and not form.is_valid():
        return Material.objects.none()
    materials = Material.objects.filter(is_active=True)
    if form.is_bound and form.cleaned_data.get('material'):
        materials = materials.filter(pk=form.cleaned_data['material'].pk)
    in_scope = Q(movements__in=movements.values('pk'))
    consumed = Q(movements__movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
    produced = Q(movements__movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE)
    return materials.annotate(
        filtered_consumed=Abs(Sum('movements__quantity', filter=in_scope & consumed)),
        filtered_produced=Sum('movements__quantity', filter=in_scope & produced),
        filtered_net=Coalesce(Sum('movements__quantity', filter=in_scope), Decimal('0')),
    ).order_by('name')


def _filtered_material_movements(request):
    # Approved jobs only. No per-user scoping: the page is manager-only.
    form = MaterialFilterForm(request.GET or None)
    movements = (
        StockMovement.objects.filter(work_order__status=WorkOrder.Status.APPROVED)
        .select_related('material', 'work_order', 'work_order__created_by')
        .annotate(typed_quantity=Abs('quantity'))
        .order_by('-work_order__performed_on', '-id')
    )
    return form, form.filter(movements)


def _date_preset_links(request):
    """One quick-range link per `DATE_PRESETS` entry.

    Keeps the other filters, drops `page`, and writes ISO dates. `active` marks
    the range currently applied.
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


def _list_page_context(request, rows):
    """`page_obj`, `querystring` (minus `page`) and `date_presets` — every filtered list needs all three."""
    querystring = request.GET.copy()
    querystring.pop('page', None)
    return {
        'page_obj': Paginator(rows, HISTORY_PAGE_SIZE).get_page(request.GET.get('page')),
        'querystring': querystring.urlencode(),
        'date_presets': _date_preset_links(request),
    }


# Excel under cs splits a .csv on `;` and needs the BOM to read UTF-8. The `;`
# also frees the comma for decimals.
CSV_DELIMITER = ';'
# Escaped because the character is invisible in an editor.
CSV_BOM = '﻿'


def _csv_number(value, decimal_pos, blank=''):
    """A Decimal with the decimal comma. None (unknown) is a blank cell, which SUM skips."""
    if value is None:
        return blank
    return number_format(value, decimal_pos=decimal_pos)


def _csv_response(stem, header, rows):
    """`rows` as a CSV download named `<stem>-<today>.csv`."""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f'{stem}-{timezone.localdate().isoformat()}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write(CSV_BOM)
    writer = csv.writer(response, delimiter=CSV_DELIMITER, lineterminator='\r\n')
    writer.writerow(header)
    writer.writerows(rows)
    return response


@role_required(*REVIEWER_ROLES)
def machine_dashboard_export(request):
    """„Stav strojů" as a CSV, built from the same helpers as the page."""
    form, usages = _filtered_machine_usages(request)
    return _csv_response(
        'stav-stroju',
        [
            'Stroj',
            'Hodiny',
            'Tuny',
            'Sazba (Kč/hod)',
            'Cena (Kč/t)',
            'Cena za hodiny (Kč)',
            'Cena za tuny (Kč)',
            'Celkem (Kč)',
        ],
        [
            [
                machine.name,
                _csv_number(machine.filtered_hours or Decimal('0'), 1),
                _csv_number(machine.filtered_tons, 2),
                _csv_number(machine.hourly_rate, 2),
                _csv_number(machine.rate_per_ton, 2),
                _csv_number(machine.filtered_hours_cost, 2),
                _csv_number(machine.filtered_tons_cost, 2),
                _csv_number(machine.filtered_total_cost, 2),
            ]
            for machine in _machine_summary(usages, form)
        ],
    )


@role_required(*REVIEWER_ROLES)
def material_dashboard_export(request):
    """„Souhrn materiálů" as a CSV, built from the same helpers as the page."""
    form, movements = _filtered_material_movements(request)
    return _csv_response(
        'souhrn-materialu',
        ['Materiál', 'Spotřebováno (t)', 'Vyrobeno (t)', 'Rozdíl (t)'],
        [
            [
                material.name,
                _csv_number(material.filtered_consumed or Decimal('0'), 2),
                _csv_number(material.filtered_produced or Decimal('0'), 2),
                _csv_number(material.filtered_net, 2),
            ]
            for material in _material_summary(movements, form)
        ],
    )


@role_required(*REVIEWER_ROLES)
def material_detail_export(request):
    """„Detail položek" as a CSV — the one row-level download.

    Every other export is a summary table; this one is the line items behind
    Materiál's, because they are the only rows in the app that name what a job
    consumed and produced and a manager reconciling a month needs them one per
    line rather than totalled.

    It is the whole filtered set, not the page that happened to be on screen:
    `page_obj` paginates at `HISTORY_PAGE_SIZE`, and a file holding rows 1–50
    of 300 under a heading that says otherwise is worse than no file. The
    filter is what the download follows, exactly as it does above.
    """
    _, movements = _filtered_material_movements(request)
    return _csv_response(
        'detail-polozek',
        ['Provedeno', 'Materiál', 'Druh', 'Množství (t)', 'Zakázka', 'Kým'],
        [
            [
                # ISO, like the „Provedeno" column on the page, which writes
                # its format explicitly rather than taking the locale's (see
                # localization.md). Excel reads an ISO date under `cs` too.
                movement.work_order.performed_on.isoformat(),
                movement.material.name,
                movement.get_movement_type_display(),
                # Positive, like the page: the form asked for a positive
                # number even though a consumed row is stored negative.
                _csv_number(movement.typed_quantity, 2),
                # The page prints „—" for a job with no description; a blank
                # cell is the same thing without the text in a data column.
                movement.work_order.description,
                movement.work_order.created_by.username,
            ]
            for movement in movements
        ],
    )


@login_required
def time_worked_export(request):
    """Hodiny's „Souhrn" as a CSV, scoped exactly like the page."""
    _, _, summary = _time_worked_scope(request)
    return _csv_response(
        'souhrn-hodin',
        ['Pracovník', 'Hodiny', 'Zakázek'],
        [[row['user'].username, _csv_number(row['hours'], 1), row['orders']] for row in summary],
    )


def _time_worked_summary(work_orders):
    """Labour hours per person across `work_orders`. Unrelated to machine motohodiny."""
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


def _time_worked_scope(request):
    """Hodiny's filter form, scoped jobs and summary — shared with its export so both scope alike."""
    form = TimeWorkedFilterForm(request.GET or None, user=request.user)
    work_orders = WorkOrder.objects.filter(status=WorkOrder.Status.APPROVED)
    if not request.user.is_manager_or_admin:
        # Enforced here, not just by hiding the `worker` filter.
        work_orders = work_orders.filter(participation_filter(request.user))
    work_orders = form.filter(work_orders)

    # Re-query by pk: the collaborator M2M join would inflate the sums.
    scoped = WorkOrder.objects.filter(pk__in=work_orders.values('pk'))

    summary = _time_worked_summary(scoped)
    if not request.user.is_manager_or_admin:
        # A collaborated job would otherwise show its creator's total.
        summary = [row for row in summary if row['user'] == request.user]
    return form, scoped, summary


@login_required
def time_worked(request):
    form, scoped, summary = _time_worked_scope(request)
    detail = (
        scoped.annotate(total_hours=Sum('worker_hours__hours'))
        .select_related('created_by')
        .prefetch_related('collaborators', 'worker_hours__user')
        .order_by('-performed_on', '-created_at')
    )
    return render(
        request,
        'workorders/time_worked.html',
        {
            'form': form,
            'summary': summary,
            'total_hours': sum((row['hours'] for row in summary), Decimal('0')),
            'my_jobs': _my_recent_jobs(request.user),
            **_list_page_context(request, detail),
        },
    )


def _my_recent_jobs(user):
    """A worker's latest submissions with their status — the only review feedback they get.

    By `created_by` alone and unfiltered; empty for managers, who have Přehled.
    """
    if user.is_manager_or_admin:
        return WorkOrder.objects.none()
    return (
        WorkOrder.objects.filter(created_by=user)
        .annotate(my_hours=Sum('worker_hours__hours', filter=Q(worker_hours__user=user)))
        # By entry time, so a job just back-dated still shows at the top.
        .order_by('-created_at')[:MY_JOBS_LIMIT]
    )
