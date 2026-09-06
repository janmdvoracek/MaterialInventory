import csv
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Abs, Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import localize, number_format
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User
from materials.models import Machine, Material

from .forms import (
    JOB_SECTIONS,
    ConsumedFormSet,
    JobFilterForm,
    MachineFilterForm,
    MachineUsageFormSet,
    MaterialFilterForm,
    ProducedFormSet,
    TimeWorkedFilterForm,
    WorkerHoursFormSet,
    WorkOrderForm,
    job_row_formset,
)
from .models import MachineUsage, StockMovement, WorkerHours, WorkOrder

HISTORY_PAGE_SIZE = 50
# How many of their own recent jobs a worker sees at the top of Hodiny. Short on
# purpose: it is a status check, not a history — the full one is the manager's
# Přehled.
MY_JOBS_LIMIT = 5
# Reviewing a job is a manager/admin job. Gated on the view, not just by hiding
# the nav entry — a hidden link is not access control.
REVIEWER_ROLES = (User.Role.MANAGER, User.Role.ADMIN)


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


def _collect_rows(consumed_formset, produced_formset, machine_formset, worker_formset):
    """The filled-in rows of a submitted job, one collection per section.

    Rows are taken exactly as typed — repeated material rows stay separate line
    items. Only the worker rows are combined: naming the same person twice adds
    their hours up rather than tripping `unique_worker_hours_per_work_order`.
    """
    consumed_rows = [f.cleaned_data for f in consumed_formset if f.cleaned_data.get('material')]
    produced_rows = [f.cleaned_data for f in produced_formset if f.cleaned_data.get('material')]
    machine_rows = [f.cleaned_data for f in machine_formset if f.cleaned_data.get('machine')]
    worker_hours = defaultdict(Decimal)
    for form in worker_formset:
        if form.cleaned_data.get('user'):
            worker_hours[form.cleaned_data['user']] += form.cleaned_data['hours']
    return consumed_rows, produced_rows, machine_rows, worker_hours


def _balance_error(consumed_rows, produced_rows):
    """The Czech complaint about a job that doesn't add up, or None.

    Mass balance: a transformation moves material between fractions, it does not
    create or destroy it, so the two sides have to add up. Compared as totals,
    not row by row — one input is normally crushed into several output
    fractions. Exact Decimal equality; the form accepts 2 decimal places and
    Decimal('5.0') == Decimal('5'), so trailing zeros don't matter. There is no
    database state behind this, which is why it runs before the write rather
    than in a model `clean()`.
    """
    if not consumed_rows or not produced_rows:
        return 'Přidejte alespoň jednu položku spotřeby a jednu položku výroby.'
    consumed_total = sum((row['quantity'] for row in consumed_rows), Decimal('0'))
    produced_total = sum((row['quantity'] for row in produced_rows), Decimal('0'))
    if consumed_total != produced_total:
        # localize() and not an f-string: the rest of the UI shows 9,5 and this
        # message would otherwise print 9.5.
        return (
            'Celkové množství spotřeby a výroby se musí rovnat '
            f'(spotřeba {localize(consumed_total)}, výroba {localize(produced_total)}).'
        )
    return None


def _write_job_rows(work_order, author, own_hours, consumed_rows, produced_rows, machine_rows, worker_hours):
    """Make the job's line items, hours and machine usage match what was typed.

    Used by both the worker's own submission and a manager's later edit, so it
    clears what is there before writing. The caller must already be inside a
    `transaction.atomic()`: the three record types are one job and must not land
    half-written. `author` — not the person doing the editing — owns the rows.
    """
    # Collaborators are derived from the hours rows, so the two can't disagree
    # about who worked the job.
    work_order.collaborators.set(worker_hours.keys())
    work_order.worker_hours.all().delete()
    WorkerHours.objects.create(work_order=work_order, user=author, hours=own_hours)
    for user, hours in worker_hours.items():
        WorkerHours.objects.create(work_order=work_order, user=user, hours=hours)

    work_order.movements.all().delete()
    for row in consumed_rows:
        StockMovement.objects.create(
            material=row['material'],
            quantity=-row['quantity'],
            movement_type=StockMovement.MovementType.TRANSFORM_CONSUME,
            work_order=work_order,
            created_by=author,
        )
    for row in produced_rows:
        StockMovement.objects.create(
            material=row['material'],
            quantity=row['quantity'],
            movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE,
            work_order=work_order,
            created_by=author,
        )

    work_order.machine_usages.all().delete()
    for row in machine_rows:
        MachineUsage.objects.create(
            work_order=work_order,
            machine=row['machine'],
            hours=row['hours'],
            tons=row['tons'],
        )


# What the two row buttons under each section post under. A submit button
# reaches the server only when it is the one that was pressed, so the key being
# there at all is the whole signal — there is no value to compare against.
ADD_ROW_BUTTONS = {f'add_{prefix}': prefix for prefix, _ in JOB_SECTIONS}
REMOVE_ROW_BUTTONS = {f'remove_{prefix}': prefix for prefix, _ in JOB_SECTIONS}

# Floor on the rows a section can be left with. „− odebrat řádek" stops here, and
# so does a rebuild from a POST that claims fewer: a section with no rows at all
# would render as a bare heading, and the only way back would be the add button
# next to it. Enforced on every section on every rebuild, not just the one whose
# button was pressed, so a hand-edited TOTAL_FORMS cannot empty a section either.
MIN_ROWS_PER_SECTION = 1

# Ceiling on the rows one section can be rebuilt with. Growing a section reads
# TOTAL_FORMS straight out of the POST rather than through a management form, so
# it needs its own limit: without one, a hand-edited value is a cheap way to make
# the server build a million form objects. 1000 is what the rendered management
# form already advertises as MAX_NUM_FORMS, so a legitimate page never hits it —
# and a form that somehow did would be refused by the formset on submit. At
# exactly the cap the new blank row is dropped, because Django will not add an
# extra beyond its own max_num either; there is nothing to do about that and
# nothing that depends on it.
MAX_ROWS_PER_SECTION = 1000


def _pressed_row_button(post_data):
    """Which section a row button was pressed for and which way, or None.

    Returns `(prefix, delta)` — delta `+1` for „+ další řádek", `-1` for
    „− odebrat řádek". None means this POST is an ordinary submission.
    """
    for name, prefix in ADD_ROW_BUTTONS.items():
        if name in post_data:
            return prefix, 1
    for name, prefix in REMOVE_ROW_BUTTONS.items():
        if name in post_data:
            return prefix, -1
    return None


def _submitted_rows(post_data, row_form, prefix):
    """One section's rows, as the raw strings the browser posted.

    Read out of the POST directly rather than off a bound formset, because the
    resized page is rebuilt unbound — see `_resized_job_forms`. `base_fields` is
    the row's own field list, so a column added to a row form is carried over
    here without a second list to keep in step.
    """
    try:
        count = int(post_data.get(f'{prefix}-TOTAL_FORMS', 0))
    except (TypeError, ValueError):
        # A management form that does not parse describes no rows. The section
        # comes back as a single blank row, which is the honest answer: there is
        # nothing to restore.
        count = 0
    return [
        {name: post_data.get(f'{prefix}-{index}-{name}', '') for name in row_form.base_fields}
        for index in range(min(count, MAX_ROWS_PER_SECTION))
    ]


def _submitted_author(post_data, submitter):
    """Who „Zapsat za" currently names, on a page being re-rendered rather than submitted.

    The collaborator list is built from the author, so resizing the
    Spolupracovníci section has to resolve it the way a real submission would.
    Otherwise a manager recording for someone else would get their own name back
    in the list, and a collaborator they had already picked would drop out of
    the queryset and off the row that was re-rendered.

    The form is bound only to read that one field. Everything else about it is
    very likely incomplete — that is the normal state of a form somebody is
    still filling in — and its errors are thrown away. A plain worker has no
    `author` field at all, so this is just `submitter` for them.
    """
    form = WorkOrderForm(post_data, user=submitter)
    form.is_valid()
    return form.author_or(submitter)


def _resized_section(rows, delta):
    """`rows` with the pressed button applied: one blank row more, or one row less.

    Returns the rows to keep and how many blank ones to pad with, because
    growing adds a row the POST has no values for while shrinking just drops the
    last set it does. The floor is applied to both, and to `delta == 0` — the
    three sections whose button was not pressed — so no section can come back
    empty however the POST was edited.

    The row that goes is the *last* one, which is the exact opposite of the one
    that arrives. Anything cleverer (drop the last empty row, say) would make
    the button hard to predict from looking at it, and nothing here is saved:
    what is on screen is the whole state.
    """
    if delta > 0:
        blank_rows = 1
    else:
        if delta < 0 and len(rows) > MIN_ROWS_PER_SECTION:
            rows = rows[:-1]
        blank_rows = 0
    return rows, max(blank_rows, MIN_ROWS_PER_SECTION - len(rows))


def _resized_job_forms(post_data, section, delta, *, author, viewer, order_form_user=None):
    """Every form on the job page, rebuilt from `post_data` with `section` one row
    bigger or smaller. Returns the job form and the four formsets by prefix.

    Unbound throughout, and deliberately: the row buttons ask for a different
    form, they do not submit one, so the page has to come back carrying what was
    typed and complaining about nothing. A bound rebuild would render „Toto pole
    je vyžadováno." over the hours box of somebody who only wanted a fourth
    material row.

    Raw POST strings round-trip through an unbound widget without help: a pk
    string re-selects its option, and a date string reaches `<input type="date">`
    unchanged instead of going out through the `cs` DATE_INPUT_FORMATS as
    `05.09.2026`, which the widget rejects and shows blank — the same trap
    `WorkOrderForm.performed_on` carries an explicit `format` for.
    """
    order_form = WorkOrderForm(user=order_form_user)
    # Read off the form's own fields, so „Zapsat za" — which exists only on a
    # manager's form — is carried over without a second list of names here.
    # Assigned after construction because the field set is not known until then.
    order_form.initial = {name: post_data.get(name, '') for name in order_form.fields}
    formsets = {}
    for prefix, row_form in JOB_SECTIONS:
        rows, blank_rows = _resized_section(
            _submitted_rows(post_data, row_form, prefix),
            delta if prefix == section else 0,
        )
        formsets[prefix] = job_row_formset(
            row_form,
            prefix=prefix,
            rows=rows,
            blank_rows=blank_rows,
            form_kwargs={'user': author, 'viewer': viewer} if prefix == 'workers' else None,
        )
    return order_form, formsets


@login_required
def transform_create(request):
    # Neither row button is a submission: the page comes straight back one row
    # bigger or smaller, with nothing validated and nothing written. Checked
    # before the normal POST branch so that branch stays the submission path and
    # nothing else.
    resized = _pressed_row_button(request.POST) if request.method == 'POST' else None
    if resized:
        section, delta = resized
        order_form, formsets = _resized_job_forms(
            request.POST,
            section,
            delta,
            author=_submitted_author(request.POST, request.user),
            viewer=request.user,
            order_form_user=request.user,
        )
        consumed_formset = formsets['consumed']
        produced_formset = formsets['produced']
        machine_formset = formsets['machines']
        worker_formset = formsets['workers']
    elif request.method == 'POST':
        order_form = WorkOrderForm(request.POST, user=request.user)
        consumed_formset = ConsumedFormSet(request.POST, prefix='consumed')
        produced_formset = ProducedFormSet(request.POST, prefix='produced')
        machine_formset = MachineUsageFormSet(request.POST, prefix='machines')
        # Who the job belongs to is chosen on this same form, and it decides who
        # may be named as a collaborator — you cannot collaborate with yourself.
        # So the author has to be settled before the hours rows are built. A
        # form that does not validate has no author; the submitter stands in,
        # only so the invalid page can be re-rendered.
        author = order_form.author_or(request.user) if order_form.is_valid() else request.user
        worker_formset = WorkerHoursFormSet(
            request.POST, prefix='workers', form_kwargs={'user': author, 'viewer': request.user}
        )
        if (
            order_form.is_valid()
            and consumed_formset.is_valid()
            and produced_formset.is_valid()
            and machine_formset.is_valid()
            and worker_formset.is_valid()
        ):
            consumed_rows, produced_rows, machine_rows, worker_hours = _collect_rows(
                consumed_formset, produced_formset, machine_formset, worker_formset
            )
            error = _balance_error(consumed_rows, produced_rows)
            if error:
                messages.error(request, error)
            else:
                # A worker's job is a proposal until a manager signs it off, so
                # it stays out of the Hodiny/Stroje reports until then. A
                # manager has nobody above them to approve it, so theirs counts
                # straight away — and that goes for one they typed on a worker's
                # behalf too: they are the reviewer, and they just saw the work
                # written down. The *submitter* decides this, not the author.
                approved = request.user.is_manager_or_admin
                with transaction.atomic():
                    work_order = WorkOrder.objects.create(
                        created_by=author,
                        description=order_form.cleaned_data['description'],
                        performed_on=order_form.cleaned_data['performed_on'],
                        status=WorkOrder.Status.APPROVED if approved else WorkOrder.Status.PENDING,
                        reviewed_at=timezone.now() if approved else None,
                        reviewed_by=request.user if approved else None,
                    )
                    _write_job_rows(
                        work_order,
                        author,
                        order_form.cleaned_data['hours'],
                        consumed_rows,
                        produced_rows,
                        machine_rows,
                        worker_hours,
                    )
                messages.success(
                    request,
                    'Zpracování bylo zaznamenáno.'
                    if approved
                    else 'Zpracování bylo zaznamenáno a čeká na schválení vedoucím.',
                )
                return redirect('transform_create')
    else:
        order_form = WorkOrderForm(user=request.user)
        consumed_formset = ConsumedFormSet(prefix='consumed')
        produced_formset = ProducedFormSet(prefix='produced')
        machine_formset = MachineUsageFormSet(prefix='machines')
        worker_formset = WorkerHoursFormSet(
            prefix='workers', form_kwargs={'user': request.user, 'viewer': request.user}
        )
    return render(
        request,
        'workorders/transform_form.html',
        {
            'order_form': order_form,
            'consumed_formset': consumed_formset,
            'produced_formset': produced_formset,
            'machine_formset': machine_formset,
            'worker_formset': worker_formset,
        },
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


def _machine_summary(usages, form):
    """Hours and tonnage per machine over `usages`.

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
    """
    if form.is_bound and not form.is_valid():
        # Same rule as the rows below: an unusable filter shows nothing, rather
        # than a fleet of zeros under a "these are your filtered results" head.
        return Machine.objects.none()
    machines = Machine.objects.filter(is_active=True)
    if form.is_bound and form.cleaned_data.get('machine'):
        machines = machines.filter(pk=form.cleaned_data['machine'].pk)
    in_scope = Q(usages__in=usages.values('pk'))
    return machines.annotate(
        filtered_hours=Sum('usages__hours', filter=in_scope),
        filtered_tons=Sum('usages__tons', filter=in_scope),
    ).order_by('name')


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
        usages = usages.filter(work_order__performed_on__gte=data['date_from'])
    if data.get('date_to'):
        usages = usages.filter(work_order__performed_on__lte=data['date_to'])
    return form, usages


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
    if data.get('date_from'):
        movements = movements.filter(work_order__performed_on__gte=data['date_from'])
    if data.get('date_to'):
        movements = movements.filter(work_order__performed_on__lte=data['date_to'])
    return form, movements


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
        ['Stroj', 'Hodiny', 'Tuny', 'Sazba (Kč/hod)', 'Cena (Kč/t)'],
        [
            [
                machine.name,
                # A machine with no rows in range really did run zero hours,
                # which is the same `default:"0"` the template applies.
                _csv_number(machine.filtered_hours or Decimal('0'), 1),
                _csv_number(machine.filtered_tons, 2),
                _csv_number(machine.hourly_rate, 2),
                _csv_number(machine.rate_per_ton, 2),
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


def _participation_filter(user):
    """A user "worked on" a job if they submitted it or were named a collaborator."""
    return Q(created_by=user) | Q(collaborators=user)


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
        work_orders = work_orders.filter(_participation_filter(request.user))
    if form.is_bound:
        if not form.is_valid():
            # An unusable filter must not fall through to showing everything.
            work_orders = work_orders.none()
        else:
            data = form.cleaned_data
            if data.get('worker'):
                work_orders = work_orders.filter(_participation_filter(data['worker']))
            if data.get('date_from'):
                work_orders = work_orders.filter(performed_on__gte=data['date_from'])
            if data.get('date_to'):
                work_orders = work_orders.filter(performed_on__lte=data['date_to'])

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


def _trim(value):
    """A stored quantity as the entry form would have accepted it.

    The models keep more decimal places than the form allows (quantity: 3 vs 2)
    and the hours fields are stored with 2 places but typed with 1, so feeding a
    raw value back as `initial` renders `5.000` and the edit form then rejects
    its own prefill. Trailing zeros are what makes the difference, so drop them
    — without letting normalize() turn 100.00 into 1E+2.
    """
    normalized = value.normalize()
    return normalized.quantize(Decimal(1)) if normalized.as_tuple().exponent > 0 else normalized


@role_required(*REVIEWER_ROLES)
def job_dashboard(request):
    """Every recorded job, newest first, with the ones awaiting review flagged.

    No worker scoping here, unlike the other list views — the whole page is
    manager/admin only.
    """
    form = JobFilterForm(request.GET or None)
    jobs = (
        WorkOrder.objects.select_related('created_by', 'reviewed_by')
        .annotate(total_hours=Sum('worker_hours__hours'))
        .prefetch_related('worker_hours__user')
        .order_by('-performed_on', '-created_at')
    )
    if form.is_bound:
        if not form.is_valid():
            # Same rule as the other lists: an unusable filter shows nothing
            # rather than quietly handing back everything.
            jobs = jobs.none()
        else:
            data = form.cleaned_data
            if data.get('created_by'):
                jobs = jobs.filter(created_by=data['created_by'])
            if data.get('status'):
                jobs = jobs.filter(status=data['status'])
            if data.get('date_from'):
                jobs = jobs.filter(performed_on__gte=data['date_from'])
            if data.get('date_to'):
                jobs = jobs.filter(performed_on__lte=data['date_to'])
    return render(
        request,
        'workorders/job_dashboard.html',
        {
            'form': form,
            'pending_count': WorkOrder.objects.filter(status=WorkOrder.Status.PENDING).count(),
            **_list_page_context(request, jobs),
        },
    )


def _job_line_items(work_order):
    """The job's consumed and produced rows, quantities as they were typed."""
    consumed, produced = [], []
    for movement in work_order.movements.select_related('material'):
        # Consumed quantities are stored negative; the form asked for a
        # positive number and that is what the reviewer should see.
        movement.typed_quantity = abs(movement.quantity)
        if movement.movement_type == StockMovement.MovementType.TRANSFORM_CONSUME:
            consumed.append(movement)
        else:
            produced.append(movement)
    return consumed, produced


@role_required(*REVIEWER_ROLES)
def job_detail(request, pk):
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by', 'reviewed_by'), pk=pk)
    consumed, produced = _job_line_items(work_order)
    return render(
        request,
        'workorders/job_detail.html',
        {
            'work_order': work_order,
            'consumed': consumed,
            'produced': produced,
            'machine_usages': work_order.machine_usages.select_related('machine'),
            'worker_hours': work_order.worker_hours.select_related('user'),
        },
    )


@role_required(*REVIEWER_ROLES)
def job_edit(request, pk):
    """Correct a recorded job. Approving it stays a separate step — a manager
    can fix a job and still leave the sign-off to someone else."""
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by'), pk=pk)
    # The rows belong to whoever recorded the job, not to the manager editing
    # it: the "moje hodiny" field is the author's, and the collaborator list has
    # to exclude the author rather than the editor.
    author = work_order.created_by
    # Same as on the Transform form: resize the section and re-render, unbound.
    # The author is the job's, not whatever the POST says — a manager correcting
    # somebody's job cannot reassign it, and there is no „Zapsat za" field here.
    resized = _pressed_row_button(request.POST) if request.method == 'POST' else None
    if resized:
        section, delta = resized
        order_form, formsets = _resized_job_forms(request.POST, section, delta, author=author, viewer=request.user)
        consumed_formset = formsets['consumed']
        produced_formset = formsets['produced']
        machine_formset = formsets['machines']
        worker_formset = formsets['workers']
    elif request.method == 'POST':
        order_form = WorkOrderForm(request.POST)
        consumed_formset = ConsumedFormSet(request.POST, prefix='consumed')
        produced_formset = ProducedFormSet(request.POST, prefix='produced')
        machine_formset = MachineUsageFormSet(request.POST, prefix='machines')
        worker_formset = WorkerHoursFormSet(
            request.POST, prefix='workers', form_kwargs={'user': author, 'viewer': request.user}
        )
        if (
            order_form.is_valid()
            and consumed_formset.is_valid()
            and produced_formset.is_valid()
            and machine_formset.is_valid()
            and worker_formset.is_valid()
        ):
            consumed_rows, produced_rows, machine_rows, worker_hours = _collect_rows(
                consumed_formset, produced_formset, machine_formset, worker_formset
            )
            error = _balance_error(consumed_rows, produced_rows)
            if error:
                messages.error(request, error)
            else:
                with transaction.atomic():
                    work_order.description = order_form.cleaned_data['description']
                    work_order.performed_on = order_form.cleaned_data['performed_on']
                    work_order.save(update_fields=['description', 'performed_on'])
                    _write_job_rows(
                        work_order,
                        author,
                        order_form.cleaned_data['hours'],
                        consumed_rows,
                        produced_rows,
                        machine_rows,
                        worker_hours,
                    )
                messages.success(request, 'Zpracování bylo upraveno.')
                return redirect('job_detail', pk=work_order.pk)
    else:
        consumed, produced = _job_line_items(work_order)
        own_hours = work_order.worker_hours.filter(user=author).first()
        order_form = WorkOrderForm(
            initial={
                'description': work_order.description,
                'hours': _trim(own_hours.hours) if own_hours else None,
                # The job's own date, not today: a correction that touches
                # nothing else must not move it.
                'performed_on': work_order.performed_on,
            }
        )
        consumed_formset = ConsumedFormSet(
            prefix='consumed',
            initial=[
                {
                    'material': movement.material_id,
                    'quantity': _trim(movement.typed_quantity),
                }
                for movement in consumed
            ],
        )
        produced_formset = ProducedFormSet(
            prefix='produced',
            initial=[
                {
                    'material': movement.material_id,
                    'quantity': _trim(movement.typed_quantity),
                }
                for movement in produced
            ],
        )
        machine_formset = MachineUsageFormSet(
            prefix='machines',
            initial=[
                {
                    'machine': usage.machine_id,
                    'hours': _trim(usage.hours),
                    'tons': _trim(usage.tons) if usage.tons is not None else None,
                }
                for usage in work_order.machine_usages.all()
            ],
        )
        worker_formset = WorkerHoursFormSet(
            prefix='workers',
            form_kwargs={'user': author, 'viewer': request.user},
            initial=[
                {'user': row.user_id, 'hours': _trim(row.hours)} for row in work_order.worker_hours.exclude(user=author)
            ],
        )
    # The shared form body reads this label off the form, and neither of the two
    # the form sets itself fits here: a manager is correcting somebody else's
    # job, so it is neither „Moje hodiny" nor an unqualified „Odpracované
    # hodiny". Set on both branches, since an invalid POST re-renders too.
    order_form.fields['hours'].label = f'Hodiny – {author.username}'
    return render(
        request,
        'workorders/job_edit.html',
        {
            'work_order': work_order,
            'order_form': order_form,
            'consumed_formset': consumed_formset,
            'produced_formset': produced_formset,
            'machine_formset': machine_formset,
            'worker_formset': worker_formset,
        },
    )


@role_required(*REVIEWER_ROLES)
@require_POST
def job_approve(request, pk):
    work_order = get_object_or_404(WorkOrder, pk=pk)
    work_order.status = WorkOrder.Status.APPROVED
    work_order.reviewed_at = timezone.now()
    work_order.reviewed_by = request.user
    work_order.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])
    messages.success(request, 'Zpracování bylo schváleno.')
    return redirect('job_detail', pk=work_order.pk)


@role_required(*REVIEWER_ROLES)
def job_delete(request, pk):
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by'), pk=pk)
    if request.method == 'POST':
        with transaction.atomic():
            # The line items have to go first: their FK to the job is PROTECT,
            # so the job cannot be deleted while they point at it. The rest
            # would cascade, but deleting them here keeps the order explicit.
            work_order.movements.all().delete()
            work_order.machine_usages.all().delete()
            work_order.worker_hours.all().delete()
            work_order.collaborators.clear()
            work_order.delete()
        messages.success(request, 'Zpracování bylo smazáno.')
        return redirect('job_dashboard')
    consumed, produced = _job_line_items(work_order)
    return render(
        request,
        'workorders/job_confirm_delete.html',
        {
            'work_order': work_order,
            'consumed': consumed,
            'produced': produced,
            'machine_usages': work_order.machine_usages.select_related('machine'),
        },
    )
