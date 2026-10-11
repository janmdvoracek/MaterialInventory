"""The report pages (Hodiny, Stroje, Materiál), their CSV exports, and the
list-page helpers Přehled shares with them."""

import csv
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Max, OuterRef, Q, Subquery, Sum
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
from .models import MachineRefuel, MachineUsage, StockMovement, WorkerHours, WorkOrder

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
    """Stroje: one filter over per-machine totals, usage rows and fill-ups (two paginators)."""
    form, usages = _filtered_machine_usages(request)
    _, refuels = _filtered_machine_refuels(request)
    return render(
        request,
        'workorders/machine_dashboard.html',
        {
            'form': form,
            'machines': _machine_summary(usages, form),
            'refuel_machines': _refuel_summary(refuels, form),
            # No fill-ups is a real zero.
            'refuel_total_litres': refuels.aggregate(total=Sum('litres'))['total'] or Decimal('0'),
            'fuel_page': _paginated(request, refuels, FUEL_PAGE_PARAM),
            **_list_page_context(request, usages),
        },
    )


def _machine_costs(machine):
    """Set the three cost columns on one summary row, and return it.

    An unset rate means unpriced (None), not free. „Celkem" sums the priced
    sides and is None if any is unknown. `filtered_hours` (hours run) arriving
    as None is a real 0 when the machine had no rows, and unknown otherwise.
    """
    if machine.filtered_hours is None and not machine.filtered_usages:
        machine.filtered_hours = Decimal('0')
    hours_cost = None
    if machine.hourly_rate is not None and machine.filtered_hours is not None:
        hours_cost = machine.filtered_hours * machine.hourly_rate
    tons_cost = None
    if machine.rate_per_ton is not None and machine.filtered_tons is not None:
        tons_cost = machine.filtered_tons * machine.rate_per_ton
    # An unset rate drops its side; a set rate with an unknown quantity makes the total unknown.
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
    """Meter reading, hours run, tonnage and cost per machine over `usages`, as a list.

    Every active machine is listed, or just the filtered one. `filtered_reading`
    is the highest reading (never a sum); `filtered_hours` is hours run, which
    the cost prices.
    """
    if form.is_bound and not form.is_valid():
        return []
    machines = Machine.objects.filter(is_active=True)
    if form.is_bound and form.cleaned_data.get('machine'):
        machines = machines.filter(pk=form.cleaned_data['machine'].pk)
    in_scope = Q(usages__in=usages.values('pk'))
    run_hours = _run_hours_by_machine(usages)
    machines = machines.annotate(
        filtered_reading=Max('usages__hours', filter=in_scope),
        filtered_tons=Sum('usages__tons', filter=in_scope),
        filtered_usages=Count('usages', filter=in_scope),
    ).order_by('name')
    for machine in machines:
        machine.filtered_hours = run_hours.get(machine.pk)
    return [_machine_costs(machine) for machine in machines]


def _run_hours_by_machine(usages):
    """Hours run per machine over `usages`: the known `run_hours`, summed.

    A machine missing from the result had no row with a known run time.
    """
    totals = {}
    for machine_id, hours in usages.values_list('machine', 'run_hours'):
        if hours is not None:
            totals[machine_id] = totals.get(machine_id, Decimal('0')) + hours
    return totals


def _with_run_hours(usages):
    """Annotate each usage with `run_hours`: its reading minus the machine's previous approved reading.

    "Previous" is the next lower reading (tie-broken on pk), not the previous
    date, because the meter only counts up and jobs arrive late. The baseline
    is every approved row, not just the filtered ones. A blank or first
    reading has no run time (None).
    """
    previous = (
        MachineUsage.objects.filter(
            machine=OuterRef('machine'),
            work_order__status=WorkOrder.Status.APPROVED,
            hours__isnull=False,
        )
        .filter(Q(hours__lt=OuterRef('hours')) | Q(hours=OuterRef('hours'), pk__lt=OuterRef('pk')))
        .order_by('-hours', '-pk')
        .values('hours')[:1]
    )
    return usages.annotate(previous_hours=Subquery(previous), run_hours=F('hours') - F('previous_hours'))


def _filtered_machine_usages(request):
    # Approved jobs only. No per-user scoping: the page is manager-only.
    form = MachineFilterForm(request.GET or None)
    usages = (
        MachineUsage.objects.filter(work_order__status=WorkOrder.Status.APPROVED)
        .select_related('machine', 'work_order', 'work_order__created_by')
        .order_by('-work_order__performed_on', '-id')
    )
    return form, form.filter(_with_run_hours(usages))


def _filtered_machine_refuels(request):
    """Stroje's fill-up rows, approved only. `MachineFilterForm`'s lookups apply unchanged."""
    form = MachineFilterForm(request.GET or None)
    refuels = (
        MachineRefuel.objects.filter(work_order__status=WorkOrder.Status.APPROVED)
        .select_related('machine', 'work_order', 'work_order__created_by')
        .order_by('-work_order__performed_on', '-id')
    )
    return form, form.filter(refuels)


def _refuel_summary(refuels, form):
    """Litres and fill-up count per active machine over `refuels`.

    Listed like `_machine_summary`, but zeros are real (`litres` is not nullable).
    """
    if form.is_bound and not form.is_valid():
        return Machine.objects.none()
    machines = Machine.objects.filter(is_active=True)
    if form.is_bound and form.cleaned_data.get('machine'):
        machines = machines.filter(pk=form.cleaned_data['machine'].pk)
    in_scope = Q(refuels__in=refuels.values('pk'))
    return machines.annotate(
        filtered_litres=Coalesce(Sum('refuels__litres', filter=in_scope), Decimal('0')),
        filtered_refuels=Count('refuels', filter=in_scope),
    ).order_by('name')


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
        .select_related('material', 'work_order', 'work_order__created_by', 'work_order__location')
        .annotate(typed_quantity=Abs('quantity'))
        .order_by('-work_order__performed_on', '-id')
    )
    return form, form.filter(movements)


# One page parameter per paginated table: Stroje has two, which page independently.
PAGE_PARAM = 'page'
FUEL_PAGE_PARAM = 'fuel_page'
PAGE_PARAMS = (PAGE_PARAM, FUEL_PAGE_PARAM)


def _paginated(request, rows, param=PAGE_PARAM):
    """One table's `page_obj`, its links' `querystring` (all but its own page param), and `page_param`."""
    querystring = request.GET.copy()
    querystring.pop(param, None)
    return {
        'page_obj': Paginator(rows, HISTORY_PAGE_SIZE).get_page(request.GET.get(param)),
        'querystring': querystring.urlencode(),
        'page_param': param,
    }


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
        for param in PAGE_PARAMS:
            params.pop(param, None)
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
    """A filtered list page's context: `page_obj`, `querystring`, `page_param` and `date_presets`.

    Take all of it; without `querystring` the filter vanishes on page two.
    """
    return {
        **_paginated(request, rows),
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
            'Stav motohodin',
            'Hodin za období',
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
                _csv_number(machine.filtered_reading, 1),
                _csv_number(machine.filtered_hours, 1),
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
def machine_refuel_export(request):
    """„Tankování" as a CSV, built from the same helpers as the card."""
    form, refuels = _filtered_machine_refuels(request)
    return _csv_response(
        'tankovani',
        ['Stroj', 'Počet tankování', 'Natankováno (l)'],
        [
            [machine.name, machine.filtered_refuels, _csv_number(machine.filtered_litres, 2)]
            for machine in _refuel_summary(refuels, form)
        ],
    )


@role_required(*REVIEWER_ROLES)
def machine_refuel_detail_export(request):
    """„Detail tankování" as a CSV: every filtered fill-up, not just the page on screen."""
    _, refuels = _filtered_machine_refuels(request)
    return _csv_response(
        'detail-tankovani',
        ['Provedeno', 'Stroj', 'Natankováno (l)', 'Zakázka', 'Kým'],
        [
            [
                refuel.work_order.performed_on.isoformat(),
                refuel.machine.name,
                _csv_number(refuel.litres, 2),
                refuel.work_order.description,
                refuel.work_order.created_by.username,
            ]
            for refuel in refuels
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
    """„Detail položek" as a CSV: every filtered line item, not just the page on screen."""
    _, movements = _filtered_material_movements(request)
    return _csv_response(
        'detail-polozek',
        ['Provedeno', 'Lokace', 'Materiál', 'Druh', 'Množství (t)', 'Zakázka', 'Kým'],
        [
            [
                movement.work_order.performed_on.isoformat(),
                # Missing values are blank cells, not the page's „—".
                movement.work_order.location.name if movement.work_order.location else '',
                movement.material.name,
                movement.get_movement_type_display(),
                # Positive, as typed; consumed rows are stored negative.
                _csv_number(movement.typed_quantity, 2),
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
