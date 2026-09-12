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
from materials.models import Machine, Material

from .forms import MachineFilterForm, MaterialFilterForm, TimeWorkedFilterForm, participation_filter
from .models import MachineUsage, StockMovement, WorkerHours, WorkOrder

HISTORY_PAGE_SIZE = 50
# How many of their own recent jobs a worker sees at the top of Hodiny. Short on
# purpose: it is a status check, not a history — the full one is the manager's
# Přehled.
MY_JOBS_LIMIT = 5


def _last_month(today):
    """The previous calendar month, first day to last."""
    last_day = today.replace(day=1) - timedelta(days=1)
    return last_day.replace(day=1), last_day


# Quick ranges offered above every filter form. Each entry maps today's date to
# the (from, to) pair the links should set — `None` clears that end of the
# range, which is what makes "Vše" the way back out of a range on a phone, where
# emptying a date input by hand is fiddly. The spans include today, so
# "posledních 7 dní" is today plus the six before it.
DATE_PRESETS = (
    ('all', 'Vše', lambda today: (None, None)),
    ('7d', 'Posledních 7 dní', lambda today: (today - timedelta(days=6), today)),
    ('30d', 'Posledních 30 dní', lambda today: (today - timedelta(days=29), today)),
    ('last_month', 'Minulý měsíc', _last_month),
)


@role_required(*REVIEWER_ROLES)
def machine_dashboard(request):
    """Stroje: the filter, per-machine totals under it, the usage rows below.

    Laid out like Hodiny, and for the same reason — the totals and the rows are
    the same data at two zoom levels, so one filter drives both and you can read
    a number and then see what it is made of without changing page.
    """
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
    """Attach the three money columns to one summary row, and return it.

    In Python rather than as annotations: the rule below is conditional, and a
    `Case`/`When` over an aggregate would be much harder to read than a loop
    over a table that is one row per active machine.

    **An unset rate means the machine is not priced that way — it does not mean
    free.** So that side of the bill is unknown and renders as a dash, the same
    reading the rate column beside it already gets. `filtered_hours` is a real
    zero, though (a priced machine that did not run last week cost nothing), so
    a machine with an `hourly_rate` and no rows in range is `0 Kč` and not a
    dash.

    **„Celkem" adds up only the sides the machine is priced on, and is itself
    unknown when any of them is.** A machine billed per tonne whose usage rows
    predate the `tons` column has a real cost nobody can compute; printing the
    hours half of it under a „Celkem" heading would understate the bill, which
    is worse than admitting the number is not available.
    """
    hours_cost = None
    if machine.hourly_rate is not None:
        hours_cost = (machine.filtered_hours or Decimal('0')) * machine.hourly_rate
    tons_cost = None
    if machine.rate_per_ton is not None and machine.filtered_tons is not None:
        tons_cost = machine.filtered_tons * machine.rate_per_ton
    # Keyed off the *rates*, not off the two costs: an unset rate drops that
    # side out of the total, while a rate with no tonnage behind it keeps the
    # side in and makes the total unknown.
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
    """Hours, tonnage and cost per machine over `usages`, as a list of rows.

    A list and not a queryset, because the three money columns are computed per
    row — see `_machine_costs`. Everything reading this only iterates or
    indexes it, and the invalid-filter case is an empty list rather than an
    empty queryset.

    A machine stores no totals of its own — these are summed from the usage
    rows on every render, over exactly the rows the page's own filter allows
    (`usages`). That is what keeps a total and the detail rows below it from
    ever disagreeing, and it is why both respect the approval status: a job
    still waiting for a manager is not counted here.

    Every active machine is listed, not just the ones with rows in range: with
    no filter that is the full fleet, and under a date filter a machine sitting
    at 0 h is the answer to "what ran last week". Naming a machine in the filter
    narrows the list to it, since the rest would be a column of zeros nobody
    asked for.

    `tons` is nullable — rows written before the column existed mean *unknown*,
    not zero — and `Sum` skips NULLs, so a machine with no recorded tonnage sums
    to `None` and renders as a dash. `0 t` would claim it processed nothing.
    That unknown propagates into the money columns; `_machine_costs` has the
    rule.
    """
    if form.is_bound and not form.is_valid():
        # Same rule as the rows below: an unusable filter shows nothing, rather
        # than a fleet of zeros under a "these are your filtered results" head.
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
    # No per-user scoping here, unlike `time_worked`: `machine_dashboard` is
    # `role_required(*REVIEWER_ROLES)`, so everyone who reaches this point sees
    # the whole depot anyway.
    form = MachineFilterForm(request.GET or None)
    # Unapproved jobs are proposals, not evidence — they stay out of the ledger
    # until a manager signs them off.
    # A usage row has no date of its own — the job's `performed_on` is its date,
    # and `-id` only breaks ties within a day.
    usages = (
        MachineUsage.objects.filter(work_order__status=WorkOrder.Status.APPROVED)
        .select_related('machine', 'work_order', 'work_order__created_by')
        .order_by('-work_order__performed_on', '-id')
    )
    return form, form.filter(usages)


@role_required(*REVIEWER_ROLES)
def material_dashboard(request):
    """Materiál: the filter, per-material tonnage under it, the line items below.

    The same page as Stroje with `Material` in place of `Machine`, and for the
    same reason — a total and the rows it is made of are one question at two
    zoom levels, so one filter drives both. This is the only place the line
    items a job records are read back across jobs; `job_detail` shows them one
    job at a time, which does not answer "how much 8/16 did we make last month".
    """
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
    """Consumed, produced and net tonnage per material over `movements`.

    Summed from the line items on every render, over exactly the rows the
    page's own filter allows, which is what keeps a total and the detail rows
    below it from ever disagreeing and is why both ignore a job still waiting
    for a manager.

    Quantities are stored signed — consumed negative, produced positive — so
    the net is simply the unfiltered sum of a material's rows, no second query
    and no subtraction. It is the useful column for a material that is both an
    input and an output. `filtered_consumed` is wrapped in `Abs` for the same
    reason `_job_line_items` takes `abs()`: the form asked for a positive
    number and that is what the reader should see.

    Every active material is listed, not just the ones with rows in range: a
    material sitting at zero is the answer to "we made no 8/16 last month",
    and an absent row cannot be told from one nobody ever seeded. Naming a
    material in the filter narrows the list to it. Unlike the tonnage on Stroje
    a zero here is a real zero rather than an unknown, so the net is
    `Coalesce`d to 0 and the two sides render as 0 rather than a dash.
    """
    if form.is_bound and not form.is_valid():
        # Same rule as the rows below: an unusable filter shows nothing, rather
        # than a catalog of zeros under a "these are your filtered results" head.
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
    # No per-user scoping, for the same reason as `_filtered_machine_usages`:
    # the view is `role_required(*REVIEWER_ROLES)`, so everyone who reaches it
    # sees the whole depot.
    form = MaterialFilterForm(request.GET or None)
    # Unapproved jobs are proposals, not evidence — they stay out of the ledger
    # until a manager signs them off.
    # A line item has no date of its own — the job's `performed_on` is its date,
    # and `-id` only breaks ties within a day.
    movements = (
        StockMovement.objects.filter(work_order__status=WorkOrder.Status.APPROVED)
        .select_related('material', 'work_order', 'work_order__created_by')
        .annotate(typed_quantity=Abs('quantity'))
        .order_by('-work_order__performed_on', '-id')
    )
    return form, form.filter(movements)


def _date_preset_links(request):
    """The quick-range links for a filter form, one per `DATE_PRESETS` entry.

    Plain links rather than a form field: one tap on a phone, no JS, and the
    range they set stays visible in the two date inputs afterwards because a
    bound form re-renders whatever the querystring holds. Dates go in as
    ISO — Django appends `%Y-%m-%d` to every locale's `DATE_INPUT_FORMATS`, so
    `cs` parses them, and `<input type="date">` only accepts that shape anyway.

    Every other filter value is carried over, so picking a range does not drop
    the machine or worker already chosen; `page` is dropped, because a new range
    starts at page one. `active` marks the link whose range is the one currently
    in effect, which makes the row a read-out as well as a control.
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
    """The three context keys every filtered list page needs.

    `page_obj` is the page of `rows` this request asks for, `querystring` is
    every *other* parameter so a page link carries the filters with it, and
    `date_presets` is the quick-range row at the top of the filter card. They
    are one call because a page needs all three — the presets are useless
    without the row template, and paging without the querystring drops the
    filter on the second page.
    """
    querystring = request.GET.copy()
    querystring.pop('page', None)
    return {
        'page_obj': Paginator(rows, HISTORY_PAGE_SIZE).get_page(request.GET.get('page')),
        'querystring': querystring.urlencode(),
        'date_presets': _date_preset_links(request),
    }


# The summary table of each filtered page, downloadable. One format for both
# halves of „csv/excel": a CSV that Excel opens by double-clicking, which on a
# Czech machine means two things beyond plain `csv.writer` defaults.
#
# `;` because Excel splits a .csv on the *locale's* list separator, and under cs
# that is the semicolon — a comma-delimited file lands in one column. It also
# frees the comma to be the decimal separator, which is what the numbers below
# use, so a tonnage arrives as a number rather than as text Excel refuses to sum.
#
# The BOM is what makes Excel read the file as UTF-8; without it „Štěrk" opens
# as mojibake. Browsers and LibreOffice ignore it, and Python's own `csv` reader
# skips it given `encoding='utf-8-sig'`.
#
# No dependency for any of this: openpyxl would buy formatting nobody asked for,
# and the app deliberately carries no library it does not use.
CSV_DELIMITER = ';'
# Written as an escape on purpose: the character itself is invisible in an
# editor and in a diff, and is exactly the kind of thing a stray reformat drops
# without anyone noticing until a file opens as mojibake.
CSV_BOM = '\ufeff'


def _csv_number(value, decimal_pos, blank=''):
    """A Decimal as the page renders it — comma separator, fixed decimals.

    `None` is *unknown*, not zero (a machine whose usage rows all predate the
    `tons` column, an unpriced machine), and the tables show it as a dash. A
    dash in a spreadsheet cell is text that breaks a column of numbers, so it
    comes out blank instead — which Excel leaves out of a SUM rather than
    counting as nothing. Callers that mean a real zero pass one in.
    """
    if value is None:
        return blank
    # number_format, not an f-string: the rest of the app shows 9,5 and a
    # spreadsheet that shows 9.5 under `cs` would be read as nine and a half
    # thousand or as text, depending on where it is opened.
    return number_format(value, decimal_pos=decimal_pos)


def _csv_response(stem, header, rows):
    """`rows` as a downloadable CSV named `<stem>-<today>.csv`.

    The date is in the filename because these are snapshots of a filtered
    report: a manager exporting the same page twice a month apart otherwise
    gets two files with the same name in one downloads folder.
    """
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f'{stem}-{timezone.localdate().isoformat()}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write(CSV_BOM)
    # Explicit CRLF: the default is the same, but Excel is the target reader and
    # nothing here should depend on a csv module default staying put.
    writer = csv.writer(response, delimiter=CSV_DELIMITER, lineterminator='\r\n')
    writer.writerow(header)
    writer.writerows(rows)
    return response


@role_required(*REVIEWER_ROLES)
def machine_dashboard_export(request):
    """„Stav strojů" as a CSV, over the filter the page was showing.

    Built from the same `_filtered_machine_usages` / `_machine_summary` pair as
    the page itself, so the download cannot disagree with the table it was
    started from — including the empty file an invalid filter gets, for the same
    reason the page shows an empty table rather than the whole fleet.
    """
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
                # A machine with no rows in range really did run zero hours,
                # which is the same `default:"0"` the template applies.
                _csv_number(machine.filtered_hours or Decimal('0'), 1),
                _csv_number(machine.filtered_tons, 2),
                _csv_number(machine.hourly_rate, 2),
                _csv_number(machine.rate_per_ton, 2),
                # The three money columns carry the page's dashes as blanks: an
                # unpriced side is unknown, and a 0 Kč in a spreadsheet would be
                # summed as a machine that cost nothing.
                _csv_number(machine.filtered_hours_cost, 2),
                _csv_number(machine.filtered_tons_cost, 2),
                _csv_number(machine.filtered_total_cost, 2),
            ]
            for machine in _machine_summary(usages, form)
        ],
    )


@role_required(*REVIEWER_ROLES)
def material_dashboard_export(request):
    """„Souhrn materiálů" as a CSV, over the filter the page was showing.

    Zeros here are real zeros, not the unknown `tons` is on Stroje — a material
    with no rows in range was consumed and produced nothing — so all three
    columns fall back to 0 rather than to a blank cell.
    """
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


@login_required
def time_worked_export(request):
    """The „Souhrn" table on Hodiny as a CSV, over the filter the page was showing.

    The only export a plain worker can reach, and it is scoped by the same
    `_time_worked_scope` the page uses — so a worker downloads the one row the
    page shows them, not the depot's. Scoping in the shared helper rather than
    here is what keeps the two from drifting apart.
    """
    _, _, summary = _time_worked_scope(request)
    return _csv_response(
        'souhrn-hodin',
        ['Pracovník', 'Hodiny', 'Zakázek'],
        [[row['user'].username, _csv_number(row['hours'], 1), row['orders']] for row in summary],
    )


def _time_worked_summary(work_orders):
    """Hours per person across `work_orders`.

    These are the labour hours each person typed on the Transform form, not a
    share of the job's machine runtime — two people on a 3-hour crushing job
    each report what they personally worked, so this column has no fixed
    relationship to the motohodiny `MachineUsage` records for the same job.
    """
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
    """The Hodiny filter form, the jobs it allows, and the per-person summary.

    All three in one helper because the page and its CSV export must answer the
    same question — an export that scoped a worker differently from the table
    they started it from would hand them the whole depot's hours. `Stroje` and
    `Materiál` get this for free from `_filtered_*` plus `_*_summary`; Hodiny
    scopes by participation as well as by the filter, which is the part worth
    having in exactly one place.
    """
    form = TimeWorkedFilterForm(request.GET or None, user=request.user)
    # Approved jobs only: hours a manager has not signed off yet are not
    # reportable, so they do not show up here for anyone, not even their author.
    work_orders = WorkOrder.objects.filter(status=WorkOrder.Status.APPROVED)
    if not request.user.is_manager_or_admin:
        # Workers only ever see jobs they took part in; enforced here (not just
        # by hiding the `worker` filter field) so it can't be bypassed via the
        # querystring directly.
        work_orders = work_orders.filter(participation_filter(request.user))
    work_orders = form.filter(work_orders)

    # Re-query by pk so the aggregation below joins cleanly — the collaborator
    # filters above already join the M2M, which would otherwise skew the sums.
    scoped = WorkOrder.objects.filter(pk__in=work_orders.values('pk'))

    summary = _time_worked_summary(scoped)
    if not request.user.is_manager_or_admin:
        # A collaborated job was created by someone else, so it would otherwise
        # put that person's total on a worker's screen.
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
    """A worker's own last few submissions, with the status of each.

    This is the only feedback a worker gets about the review: a job is never
    handed back to its author, and everything above filters to APPROVED, so
    without it there is no telling a job still waiting for approval from one
    that never landed. It sits on Hodiny because that is where a worker goes to
    see their hours, and a pending job is precisely the hours that are missing
    from the summary above.

    Submissions only, not jobs they were named on — a collaborator did not
    record the job, and their hours from it appear in the summary once it is
    approved. Managers get nothing here: Hodiny is the whole depot's report for
    them, and Přehled already lists every job with its status.
    """
    if user.is_manager_or_admin:
        return WorkOrder.objects.none()
    return (
        WorkOrder.objects.filter(created_by=user)
        .annotate(my_hours=Sum('worker_hours__hours', filter=Q(worker_hours__user=user)))
        # The one list still ordered by when it was typed, not when the work
        # happened: it is a recency list of *submissions*, and a job someone
        # just back-dated to last month has to appear at the top of it anyway —
        # checking on it is the whole reason the list exists. The date column
        # still shows `performed_on`, like everywhere else.
        .order_by('-created_at')[:MY_JOBS_LIMIT]
    )
