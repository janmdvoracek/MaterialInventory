"""Recording a job (Zpracování) and reviewing it (Přehled and the job pages)."""

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import localize
from django.views.decorators.http import require_POST

from accounts.decorators import REVIEWER_ROLES, role_required

from .forms import JOB_SECTIONS, JobFilterForm, WorkOrderForm, job_row_formset
from .models import MachineUsage, StockMovement, WorkerHours, WorkOrder
from .reports import _list_page_context


def _collect_rows(formsets):
    """The filled-in rows of a submitted job, one collection per section.

    Rows are taken exactly as typed. Every section refuses a repeat before this
    runs — `UniqueChoiceFormSet` — so there is nothing here to combine or to
    de-duplicate: each material, machine and person appears at most once per
    section, and the worker mapping cannot collide with
    `unique_worker_hours_per_work_order`.
    """

    def filled(prefix, field):
        return [form.cleaned_data for form in formsets[prefix] if form.cleaned_data.get(field)]

    worker_hours = {row['user']: row['hours'] for row in filled('workers', 'user')}
    return filled('consumed', 'material'), filled('produced', 'material'), filled('machines', 'machine'), worker_hours


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
    WorkerHours.objects.bulk_create(
        WorkerHours(work_order=work_order, user=user, hours=hours)
        for user, hours in [(author, own_hours), *worker_hours.items()]
    )

    work_order.movements.all().delete()
    # Consumed quantities are stored negative, so a material's net is a plain Sum.
    StockMovement.objects.bulk_create(
        StockMovement(
            work_order=work_order,
            created_by=author,
            material=row['material'],
            quantity=sign * row['quantity'],
            movement_type=movement_type,
        )
        for rows, sign, movement_type in (
            (consumed_rows, -1, StockMovement.MovementType.TRANSFORM_CONSUME),
            (produced_rows, 1, StockMovement.MovementType.TRANSFORM_PRODUCE),
        )
        for row in rows
    )

    work_order.machine_usages.all().delete()
    MachineUsage.objects.bulk_create(
        MachineUsage(work_order=work_order, machine=row['machine'], hours=row['hours'], tons=row['tons'])
        for row in machine_rows
    )


# What the two row buttons under each section post under. A submit button
# reaches the server only when it is the one that was pressed, so the key being
# there at all is the whole signal — there is no value to compare against.
ADD_ROW_BUTTONS = {f'add_{section.prefix}': section.prefix for section in JOB_SECTIONS}
REMOVE_ROW_BUTTONS = {f'remove_{section.prefix}': section.prefix for section in JOB_SECTIONS}

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


def _submitted_rows(post_data, section):
    """One section's rows, as the raw strings the browser posted.

    Read out of the POST directly rather than off a bound formset, because the
    resized page is rebuilt unbound — see `_job_forms`. `base_fields` is the
    row's own field list, so a column added to a row form is carried over here
    without a second list to keep in step.
    """
    prefix = section.prefix
    try:
        count = int(post_data.get(f'{prefix}-TOTAL_FORMS', 0))
    except (TypeError, ValueError):
        # A management form that does not parse describes no rows. The section
        # comes back as a single blank row, which is the honest answer: there is
        # nothing to restore.
        count = 0
    return [
        {name: post_data.get(f'{prefix}-{index}-{name}', '') for name in section.row_form.base_fields}
        for index in range(min(count, MAX_ROWS_PER_SECTION))
    ]


def _submitted_author(post_data, submitter):
    """Who „Zapsat za" names in this POST, or the submitter.

    The collaborator list is built from the author, so it has to be settled
    before the formsets are — on a submission and on a row-button rebuild alike.
    Otherwise a manager recording for someone else would get their own name back
    in the list, and a collaborator they had already picked would drop out of
    the queryset and off the row.

    The form is bound only to read that one field, and its errors are thrown
    away: the page's own bound form reports them, and a form still being filled
    in is expected to have some. A plain worker has no `author` field at all, so
    this is just `submitter` for them.
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


def _section_form_kwargs(prefix, *, author, viewer, keep=None):
    """The per-row kwargs one section's forms are built with.

    The workers section needs the pair that decides who may be named on a row;
    the three catalog sections need the records an existing job already uses, so
    `job_edit` can re-render a row whose material or machine has since been
    retired. A fresh form passes no `keep` and gets the active catalog only,
    which is the whole point of retiring something.
    """
    if prefix == 'workers':
        return {'user': author, 'viewer': viewer}
    return {'keep': (keep or {}).get(prefix, ())}


def _job_forms(request, *, author, viewer, order_form_user=None, keep=None, initial=None):
    """Every form on the job page, and whether this request submitted them.

    Returns `(order_form, formsets_by_prefix, submitted)`, covering the three
    things a request to the job page can be:

    - **A row button.** Every form is rebuilt from the POST, *unbound*, with
      that section one row bigger or smaller. Asking for a row is not
      submitting, so the page has to come back carrying what was typed and
      complaining about nothing — a bound rebuild would put „Toto pole je
      vyžadováno." over the hours box of somebody who only wanted a fourth
      material row. Raw POST strings round-trip through an unbound widget
      without help: a pk re-selects its option, and a date string reaches
      `<input type="date">` unchanged instead of going out through the `cs`
      DATE_INPUT_FORMATS as `05.09.2026`, which the widget shows blank.
    - **Any other POST.** Everything bound; `submitted` is True.
    - **A GET.** Fresh forms, pre-filled from `initial` — keyed `order` for the
      job form and by section prefix for the rows — on `job_edit`.

    `author` decides who may be named as a collaborator, `viewer` how wide that
    list is, and `keep` which retired catalog records `job_edit` still offers.
    """
    form_kwargs = {
        section.prefix: _section_form_kwargs(section.prefix, author=author, viewer=viewer, keep=keep)
        for section in JOB_SECTIONS
    }
    pressed = _pressed_row_button(request.POST) if request.method == 'POST' else None
    if pressed:
        pressed_prefix, delta = pressed
        order_form = WorkOrderForm(user=order_form_user)
        # Read off the form's own fields, so „Zapsat za" — which exists only on a
        # manager's form — is carried over without a second list of names here.
        # Assigned after construction because the field set is not known until then.
        order_form.initial = {name: request.POST.get(name, '') for name in order_form.fields}
        formsets = {}
        for section in JOB_SECTIONS:
            rows, blank_rows = _resized_section(
                _submitted_rows(request.POST, section),
                delta if section.prefix == pressed_prefix else 0,
            )
            formsets[section.prefix] = job_row_formset(
                section, rows=rows, blank_rows=blank_rows, form_kwargs=form_kwargs[section.prefix]
            )
        return order_form, formsets, False
    if request.method == 'POST':
        order_form = WorkOrderForm(request.POST, user=order_form_user)
        formsets = {
            section.prefix: job_row_formset(section, data=request.POST, form_kwargs=form_kwargs[section.prefix])
            for section in JOB_SECTIONS
        }
        return order_form, formsets, True
    initial = initial or {}
    order_form = WorkOrderForm(initial=initial.get('order'), user=order_form_user)
    formsets = {
        section.prefix: job_row_formset(
            section, rows=initial.get(section.prefix, ()), form_kwargs=form_kwargs[section.prefix]
        )
        for section in JOB_SECTIONS
    }
    return order_form, formsets, False


def _valid_job_rows(request, order_form, formsets):
    """The rows of a submitted job, or None when it cannot be saved.

    Nothing is written unless all five forms validate and the job balances.
    Form errors render beside their fields; the balance error has no field to
    sit on, so it goes on `messages`.
    """
    if not (order_form.is_valid() and all(formset.is_valid() for formset in formsets.values())):
        return None
    rows = _collect_rows(formsets)
    error = _balance_error(rows[0], rows[1])
    if error:
        messages.error(request, error)
        return None
    return rows


def _job_context(order_form, formsets, **extra):
    """The job page's context: the job form, the sections in render order for
    `_job_form_fields.html`, and each formset under its own name as well."""
    return {
        'order_form': order_form,
        'sections': [(section, formsets[section.prefix]) for section in JOB_SECTIONS],
        **{section.context_name: formsets[section.prefix] for section in JOB_SECTIONS},
        **extra,
    }


@login_required
def transform_create(request):
    # Who the job belongs to is chosen on this same form, and it decides who may
    # be named as a collaborator — you cannot collaborate with yourself — so it
    # is settled before any formset is built.
    author = _submitted_author(request.POST, request.user) if request.method == 'POST' else request.user
    order_form, formsets, submitted = _job_forms(
        request, author=author, viewer=request.user, order_form_user=request.user
    )
    if submitted:
        rows = _valid_job_rows(request, order_form, formsets)
        if rows is not None:
            # A worker's job is a proposal until a manager signs it off, so it
            # stays out of the Hodiny/Stroje reports until then. A manager has
            # nobody above them to approve it, so theirs counts straight away —
            # and that goes for one they typed on a worker's behalf too: they
            # are the reviewer, and they just saw the work written down. The
            # *submitter* decides this, not the author.
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
                _write_job_rows(work_order, author, order_form.cleaned_data['hours'], *rows)
            messages.success(
                request,
                'Zpracování bylo zaznamenáno.'
                if approved
                else 'Zpracování bylo zaznamenáno a čeká na schválení vedoucím.',
            )
            return redirect('transform_create')
    return render(request, 'workorders/transform_form.html', _job_context(order_form, formsets))


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
    jobs = form.filter(jobs)
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
    # to exclude the author rather than the editor. There is no „Zapsat za" on
    # this form, so a correction cannot reassign the job.
    author = work_order.created_by
    consumed, produced = _job_line_items(work_order)
    usages = list(work_order.machine_usages.all())
    # What the job already names, so a material or machine retired since it was
    # recorded still renders on its row and survives the save — `_offer_recorded`
    # in forms.py has the reasoning. The workers section needs nothing:
    # `collaborator_queryset` never filters on `is_active`.
    keep = {
        'consumed': [movement.material_id for movement in consumed],
        'produced': [movement.material_id for movement in produced],
        'machines': [usage.machine_id for usage in usages],
    }
    order_form, formsets, submitted = _job_forms(
        request,
        author=author,
        viewer=request.user,
        keep=keep,
        initial=_edit_initial(work_order, author, consumed, produced, usages) if request.method == 'GET' else None,
    )
    if submitted:
        rows = _valid_job_rows(request, order_form, formsets)
        if rows is not None:
            with transaction.atomic():
                work_order.description = order_form.cleaned_data['description']
                work_order.performed_on = order_form.cleaned_data['performed_on']
                work_order.save(update_fields=['description', 'performed_on'])
                _write_job_rows(work_order, author, order_form.cleaned_data['hours'], *rows)
            messages.success(request, 'Zpracování bylo upraveno.')
            return redirect('job_detail', pk=work_order.pk)
    # The shared form body reads this label off the form, and neither of the two
    # the form sets itself fits here: a manager is correcting somebody else's
    # job, so it is neither „Moje hodiny" nor an unqualified „Odpracované
    # hodiny". Set on every path, since an invalid POST re-renders too.
    order_form.fields['hours'].label = f'Hodiny – {author.username}'
    return render(request, 'workorders/job_edit.html', _job_context(order_form, formsets, work_order=work_order))


def _edit_initial(work_order, author, consumed, produced, usages):
    """`job_edit`'s pre-fill: the job as recorded, quantities as they were typed."""
    own_hours = work_order.worker_hours.filter(user=author).first()
    return {
        'order': {
            'description': work_order.description,
            'hours': _trim(own_hours.hours) if own_hours else None,
            # The job's own date, not today: a correction that touches nothing
            # else must not move it.
            'performed_on': work_order.performed_on,
        },
        'consumed': [{'material': m.material_id, 'quantity': _trim(m.typed_quantity)} for m in consumed],
        'produced': [{'material': m.material_id, 'quantity': _trim(m.typed_quantity)} for m in produced],
        'machines': [
            {'machine': u.machine_id, 'hours': _trim(u.hours), 'tons': _trim(u.tons) if u.tons is not None else None}
            for u in usages
        ],
        'workers': [
            {'user': row.user_id, 'hours': _trim(row.hours)} for row in work_order.worker_hours.exclude(user=author)
        ],
    }


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
        # Line items, machine usage, hours and the collaborator links all
        # cascade, so this is the same delete the admin performs.
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
