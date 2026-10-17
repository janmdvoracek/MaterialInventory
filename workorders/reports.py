"""The report pages (Souhrn, Stroje, Materiál), their CSV exports, and the
list-page helpers Přehled shares with them."""

import csv
from datetime import timedelta
from decimal import Decimal
from functools import cached_property

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

from .forms import MachineFilterForm, MaterialFilterForm, RefuelFilterForm, TimeWorkedFilterForm, participation_filter
from .models import MachineRefuel, MachineUsage, Refuelling, StockMovement, WorkerHours, WorkOrder

HISTORY_PAGE_SIZE = 50
# A worker's own recent jobs on Souhrn: a status check, not a history.
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


def _catalog_in_scope(form, catalog, field):
    """The active `catalog` records a report's summary lists, or None if the filter is invalid.

    Every active record is listed, or just the one the filter names in `field`.
    """
    if form.is_bound and not form.is_valid():
        return None
    catalog = catalog.filter(is_active=True)
    if form.is_bound and form.cleaned_data.get(field):
        catalog = catalog.filter(pk=form.cleaned_data[field].pk)
    return catalog


class MachineReport:
    """Stroje's numbers under one filter: usage and fuel, each as a summary and its detail rows.

    Approved entries only. Not scoped per user: Stroje is manager-only, and
    Souhrn's fleet tables (`_fleet_totals`) are deliberately shared. The page,
    its four exports and the fleet tables all read this, so they can't disagree.
    """

    def __init__(self, data):
        self.form = MachineFilterForm(data)
        # The same filter, with the date and author read off the refuelling.
        self._fuel_form = RefuelFilterForm(data)

    @cached_property
    def usages(self):
        """The filtered usage rows, newest first, each with its `run_hours`."""
        usages = (
            MachineUsage.objects.filter(work_order__status=WorkOrder.Status.APPROVED)
            .select_related('machine', 'work_order', 'work_order__created_by')
            .order_by('-work_order__performed_on', '-id')
        )
        return self.form.filter(_with_run_hours(usages))

    @cached_property
    def summary(self):
        """Meter reading, hours run, tonnage and cost per machine, as a list.

        `filtered_reading` is the highest reading (never a sum);
        `filtered_hours` is hours run, which the cost prices.
        """
        machines = _catalog_in_scope(self.form, Machine.objects.all(), 'machine')
        if machines is None:
            return []
        in_scope = Q(usages__in=self.usages.values('pk'))
        run_hours = _run_hours_by_machine(self.usages)
        machines = machines.annotate(
            filtered_reading=Max('usages__hours', filter=in_scope),
            filtered_tons=Sum('usages__tons', filter=in_scope),
            filtered_usages=Count('usages', filter=in_scope),
        ).order_by('name')
        for machine in machines:
            machine.filtered_hours = run_hours.get(machine.pk)
        return [_machine_costs(machine) for machine in machines]

    @cached_property
    def fill_ups(self):
        """The filtered fill-up rows, newest first."""
        refuels = (
            MachineRefuel.objects.filter(refuelling__status=Refuelling.Status.APPROVED)
            .select_related('machine', 'source', 'refuelling', 'refuelling__created_by')
            .order_by('-refuelling__performed_on', '-id')
        )
        return self._fuel_form.filter(refuels)

    @cached_property
    def fuel_summary(self):
        """Litres and fill-up count per machine. Listed like `summary`, but zeros are real (`litres` is not nullable)."""
        machines = _catalog_in_scope(self._fuel_form, Machine.objects.all(), 'machine')
        if machines is None:
            return Machine.objects.none()
        in_scope = Q(refuels__in=self.fill_ups.values('pk'))
        return machines.annotate(
            filtered_litres=Coalesce(Sum('refuels__litres', filter=in_scope), Decimal('0')),
            filtered_refuels=Count('refuels', filter=in_scope),
        ).order_by('name')

    @cached_property
    def total_litres(self):
        # No fill-ups is a real zero.
        return self.fill_ups.aggregate(total=Sum('litres'))['total'] or Decimal('0')


@role_required(*REVIEWER_ROLES)
def machine_dashboard(request):
    """Stroje: one filter over per-machine totals, usage rows and fill-ups (two paginators)."""
    report = MachineReport(request.GET or None)
    listing = _list_page_context(request, report.usages)
    context = {
        'form': report.form,
        'machines': report.summary,
        'refuel_machines': report.fuel_summary,
        'refuel_total_litres': report.total_litres,
        'fuel_page': _paginated(request, report.fill_ups, FUEL_PAGE_PARAM),
        **listing,
    }
    context['tables'] = _previews(
        request,
        machines=context['machines'],
        refuels=context['refuel_machines'],
        refuel_rows=context['fuel_page'],
        usages=listing,
    )
    return render(request, 'workorders/machine_dashboard.html', context)


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


class MaterialReport:
    """Materiál's numbers: per-material tonnage and the line items behind it.

    Approved jobs only. No per-user scoping: the page is manager-only. The page
    and its two exports read this.
    """

    def __init__(self, data):
        self.form = MaterialFilterForm(data)

    @cached_property
    def movements(self):
        """The filtered line items, newest first, each with its `typed_quantity`."""
        movements = (
            StockMovement.objects.filter(work_order__status=WorkOrder.Status.APPROVED)
            .select_related('material', 'work_order', 'work_order__created_by', 'work_order__location')
            .annotate(typed_quantity=Abs('quantity'))
            .order_by('-work_order__performed_on', '-id')
        )
        return self.form.filter(movements)

    @cached_property
    def summary(self):
        """Consumed, produced and net tonnage per material.

        Quantities are stored signed, so the net is a plain Sum. Zeros here are
        real zeros, unlike tons on Stroje.
        """
        materials = _catalog_in_scope(self.form, Material.objects.all(), 'material')
        if materials is None:
            return Material.objects.none()
        in_scope = Q(movements__in=self.movements.values('pk'))
        consumed = Q(movements__movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        produced = Q(movements__movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE)
        return materials.annotate(
            filtered_consumed=Abs(Sum('movements__quantity', filter=in_scope & consumed)),
            filtered_produced=Sum('movements__quantity', filter=in_scope & produced),
            filtered_net=Coalesce(Sum('movements__quantity', filter=in_scope), Decimal('0')),
        ).order_by('name')

    @cached_property
    def detail_rows(self):
        """„Detail položek": the line items, also narrowed by „Druh pohybu"."""
        return self.form.detail_rows(self.movements)


@role_required(*REVIEWER_ROLES)
def material_dashboard(request):
    """Materiál: the filter, per-material tonnage, and the line items behind it."""
    report = MaterialReport(request.GET or None)
    listing = _list_page_context(request, report.detail_rows)
    context = {'form': report.form, 'materials': report.summary, **listing}
    context['tables'] = _previews(request, materials=context['materials'], movements=listing)
    return render(request, 'workorders/material_dashboard.html', context)


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


# A report or list table shows its first PREVIEW_ROWS rows until its „Zobrazit
# vše" link adds the table's key to EXPAND_PARAM; „Zobrazit méně" takes it out.
# Exports ignore both and hold every row.
PREVIEW_ROWS = 5
EXPAND_PARAM = 'vse'


def _previews(request, **tables):
    """`_preview` for each `key=rows` table on a page."""
    return {key: _preview(request, key, rows) for key, rows in tables.items()}


def _preview(request, key, rows):
    """One table's `rows`, cut to PREVIEW_ROWS until the table is expanded.

    `rows` is a list, a queryset, or a paginated table as `_paginated` returns
    it. `total` is every row the expanded table holds, across all its pages; a
    paginated table shows its page links only once expanded. Both links keep
    the filters and the other tables' state, and jump back to this table
    (`anchor`). Collapsing also drops the table's own page param, so expanding
    it again starts on page one.
    """
    expanded = key in request.GET.getlist(EXPAND_PARAM)
    page_param = None
    if isinstance(rows, dict):
        page_param, page = rows['page_param'], rows['page_obj']
        total, items = page.paginator.count, page.object_list
    else:
        total, items = len(rows), rows
    shown = items if expanded else list(items[:PREVIEW_ROWS])
    expand = request.GET.copy()
    expand.appendlist(EXPAND_PARAM, key)
    collapse = request.GET.copy()
    collapse.setlist(EXPAND_PARAM, [value for value in collapse.getlist(EXPAND_PARAM) if value != key])
    if page_param:
        collapse.pop(page_param, None)
    return {
        'rows': shown,
        'expanded': expanded,
        'total': total,
        'has_more': not expanded and total > len(shown),
        'can_collapse': expanded and total > PREVIEW_ROWS,
        'expand_querystring': expand.urlencode(),
        'collapse_querystring': collapse.urlencode(),
        'anchor': f'tabulka-{key}',
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
        for field, day in zip(('date_from', 'date_to'), span(today), strict=True):
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
    """„Stav strojů" as a CSV, from the page's own `MachineReport`."""
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
            for machine in MachineReport(request.GET or None).summary
        ],
    )


@role_required(*REVIEWER_ROLES)
def machine_refuel_export(request):
    """„Tankování" as a CSV, from the page's own `MachineReport`."""
    return _csv_response(
        'tankovani',
        ['Stroj', 'Počet tankování', 'Natankováno (l)'],
        [
            [machine.name, machine.filtered_refuels, _csv_number(machine.filtered_litres, 2)]
            for machine in MachineReport(request.GET or None).fuel_summary
        ],
    )


@role_required(*REVIEWER_ROLES)
def machine_refuel_detail_export(request):
    """„Detail tankování" as a CSV: every filtered fill-up, not just the page on screen."""
    return _csv_response(
        'detail-tankovani',
        ['Provedeno', 'Stroj', 'Zdroj', 'Natankováno (l)', 'Kým'],
        [
            [
                refuel.refuelling.performed_on.isoformat(),
                refuel.machine.name,
                # Fill-ups from before sources existed have none: a blank cell.
                refuel.source.name if refuel.source else '',
                _csv_number(refuel.litres, 2),
                refuel.refuelling.created_by.username,
            ]
            for refuel in MachineReport(request.GET or None).fill_ups
        ],
    )


@role_required(*REVIEWER_ROLES)
def machine_usage_detail_export(request):
    """„Detail používání strojů" as a CSV: every filtered usage row, not just the page on screen."""
    return _csv_response(
        'detail-pouzivani-stroju',
        ['Provedeno', 'Stroj', 'Hodin na zakázce', 'Tuny', 'Zakázka', 'Kým'],
        [
            [
                usage.work_order.performed_on.isoformat(),
                usage.machine.name,
                # Run time from `_with_run_hours`, as on the page; a first or blank reading is a blank cell.
                _csv_number(usage.run_hours, 1),
                _csv_number(usage.tons, 2),
                usage.work_order.description,
                usage.work_order.created_by.username,
            ]
            for usage in MachineReport(request.GET or None).usages
        ],
    )


@role_required(*REVIEWER_ROLES)
def material_dashboard_export(request):
    """„Souhrn materiálů" as a CSV, from the page's own `MaterialReport`."""
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
            for material in MaterialReport(request.GET or None).summary
        ],
    )


@role_required(*REVIEWER_ROLES)
def material_detail_export(request):
    """„Detail položek" as a CSV: every filtered line item, not just the page on screen."""
    return _csv_response(
        'detail-polozek',
        ['ID', 'Provedeno', 'Lokace', 'Materiál', 'Druh', 'Množství (t)', 'Zakázka', 'Kým'],
        [
            [
                # Which job the row belongs to: two jobs can share a date, description and author.
                movement.work_order_id,
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
            for movement in MaterialReport(request.GET or None).detail_rows
        ],
    )


@login_required
def time_worked_export(request):
    """Souhrn's „Odpracované hodiny" as a CSV, scoped exactly like the page (`HoursReport`)."""
    return _csv_response(
        'souhrn-hodin',
        ['Pracovník', 'Hodiny', 'Zakázek'],
        [
            [row['user'].username, _csv_number(row['hours'], 1), row['orders']]
            for row in HoursReport(request.GET or None, request.user).summary
        ],
    )


class HoursReport:
    """Souhrn's labour numbers: hours per person and the jobs behind them.

    Approved jobs only. A worker sees only the jobs they worked on and only
    their own summary row: enforced here, in the queryset, not by hiding the
    `worker` filter. The page and its export read this.
    """

    def __init__(self, data, user):
        self.user = user
        self.form = TimeWorkedFilterForm(data, user=user)

    @cached_property
    def jobs(self):
        """The approved jobs in scope, filtered, one row per job."""
        jobs = WorkOrder.objects.filter(status=WorkOrder.Status.APPROVED)
        if not self.user.is_manager_or_admin:
            jobs = jobs.filter(participation_filter(self.user))
        # Re-query by pk: the collaborator M2M join would inflate the sums.
        return WorkOrder.objects.filter(pk__in=self.form.filter(jobs).values('pk'))

    @cached_property
    def summary(self):
        """Labour hours per person across `jobs`, most hours first. Unrelated to machine motohodiny."""
        totals = (
            WorkerHours.objects.filter(work_order__in=self.jobs)
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
        if not self.user.is_manager_or_admin:
            # A collaborated job would otherwise show its creator's total.
            rows = [row for row in rows if row['user'] == self.user]
        return rows

    @cached_property
    def total_hours(self):
        return sum((row['hours'] for row in self.summary), Decimal('0'))

    @cached_property
    def detail(self):
        """„Detail": the jobs with their total hours, newest first."""
        return (
            self.jobs.annotate(total_hours=Sum('worker_hours__hours'))
            .select_related('created_by')
            .prefetch_related('collaborators', 'worker_hours__user')
            .order_by('-performed_on', '-created_at')
        )


@login_required
def time_worked(request):
    report = HoursReport(request.GET or None, request.user)
    listing = _list_page_context(request, report.detail)
    context = {
        'form': report.form,
        'summary': report.summary,
        'total_hours': report.total_hours,
        'my_jobs': _my_recent_jobs(request.user),
        # Managers have the same numbers, with more, on Stroje.
        'fleet': None if request.user.is_manager_or_admin else _fleet_totals(request),
        **listing,
    }
    tables = {'hours': report.summary, 'jobs': listing}
    if context['fleet']:
        tables.update(machines=context['fleet']['machines'], refuels=context['fleet']['refuel_machines'])
    context['tables'] = _previews(request, **tables)
    return render(request, 'workorders/time_worked.html', context)


def _fleet_totals(request):
    """Souhrn's machine tables: hours run, tonnage and fuel per machine, from everyone's approved entries.

    Not scoped to the worker, by design: these are the machines' totals, not the
    worker's. Only the page's dates apply; Stroje's machine and author filters
    are not on this page, so anything else in the querystring is ignored.
    """
    dates = {key: request.GET[key] for key in ('date_from', 'date_to') if key in request.GET} or None
    report = MachineReport(dates)
    return {
        'machines': report.summary,
        'refuel_machines': report.fuel_summary,
        'total_litres': report.total_litres,
    }


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
