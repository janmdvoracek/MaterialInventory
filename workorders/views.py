"""Recording a job (Zpracování), reviewing it (Přehled and the job pages), and
serving the photos attached to one."""

from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import localize
from django.views.decorators.http import require_POST

from accounts.decorators import REVIEWER_ROLES, role_required

from .forms import JOB_SECTIONS, PHOTO_MAX_COUNT, JobFilterForm, WorkOrderForm, job_row_formset
from .models import JobPhoto, MachineUsage, Refuelling, StockMovement, WorkerHours, WorkOrder
from .reports import _list_page_context


class JobRows(NamedTuple):
    """Everything one job submission records, split the way the tables are."""

    consumed: list
    produced: list
    usages: list
    worker_hours: dict


def _filled_rows(formset, field):
    """The rows of one section that name something in `field`, as typed."""
    return [form.cleaned_data for form in formset if form.cleaned_data.get(field)]


def _collect_rows(formsets):
    """The filled-in rows of each section. Duplicates were already refused."""
    return JobRows(
        consumed=_filled_rows(formsets['consumed'], 'material'),
        produced=_filled_rows(formsets['produced'], 'material'),
        usages=_filled_rows(formsets['machines'], 'machine'),
        worker_hours={row['user']: row['hours'] for row in _filled_rows(formsets['workers'], 'user')},
    )


# A banner at the top: field errors far down a phone screen are easy to miss.
REQUIRED_FIELDS_MESSAGE = 'Vyplňte prosím všechna povinná pole.'
FORM_ERRORS_MESSAGE = 'Formulář obsahuje chyby, opravte prosím vyznačená pole.'


def _form_error_summary(order_form, formsets):
    """The banner for a refused entry page: the required-field wording if any error is `required`."""
    forms = [order_form, *(form for formset in formsets.values() for form in formset.forms)]
    if any(error.code == 'required' for form in forms for errors in form.errors.as_data().values() for error in errors):
        return REQUIRED_FIELDS_MESSAGE
    return FORM_ERRORS_MESSAGE


def _balance_error(consumed_rows, produced_rows):
    """The Czech error if consumed and produced totals are not exactly equal, else None."""
    if not consumed_rows or not produced_rows:
        return 'Přidejte alespoň jednu položku spotřeby a jednu položku výroby.'
    consumed_total = sum((row['quantity'] for row in consumed_rows), Decimal('0'))
    produced_total = sum((row['quantity'] for row in produced_rows), Decimal('0'))
    if consumed_total != produced_total:
        # localize() for the decimal comma the rest of the UI shows.
        return (
            'Celkové množství spotřeby a výroby se musí rovnat '
            f'(spotřeba {localize(consumed_total)}, výroba {localize(produced_total)}).'
        )
    return None


def _apply_photos(work_order, cleaned_data):
    """Delete the stored photos ticked „Odebrat", then add every new one.

    Call inside `transaction.atomic()`. New photos never replace stored ones.
    Files of removed rows are deleted on commit (`models.discard_photo`).
    """
    for photo in cleaned_data.get('remove_photos') or ():
        photo.delete()
    JobPhoto.objects.bulk_create(JobPhoto(work_order=work_order, file=upload) for upload in cleaned_data['photo'])


def _write_job_rows(work_order, author, own_hours, rows):
    """Replace the job's hours, line items and machine usage with the submitted rows.

    Call inside `transaction.atomic()`. The rows belong to `author`, not the editor.
    """
    # Derived from the hours rows, so the two can't disagree.
    work_order.collaborators.set(rows.worker_hours.keys())
    work_order.worker_hours.all().delete()
    WorkerHours.objects.bulk_create(
        WorkerHours(work_order=work_order, user=user, hours=hours)
        for user, hours in [(author, own_hours), *rows.worker_hours.items()]
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
        for section_rows, sign, movement_type in (
            (rows.consumed, -1, StockMovement.MovementType.TRANSFORM_CONSUME),
            (rows.produced, 1, StockMovement.MovementType.TRANSFORM_PRODUCE),
        )
        for row in section_rows
    )

    work_order.machine_usages.all().delete()
    MachineUsage.objects.bulk_create(
        MachineUsage(work_order=work_order, machine=row['machine'], hours=row['hours'], tons=row['tons'])
        for row in rows.usages
    )


# Applied to every section on every rebuild, so no POST can leave one empty.
MIN_ROWS_PER_SECTION = 1

# A rebuild reads TOTAL_FORMS straight from the POST, so cap it. Matches MAX_NUM_FORMS.
MAX_ROWS_PER_SECTION = 1000


def _pressed_row_button(post_data, sections):
    """`(prefix, +1 or -1)` for a pressed row button of `sections`, or None for a submission.

    Only the pressed submit button is posted, so its name being present is the signal.
    """
    for section in sections:
        if f'add_{section.prefix}' in post_data:
            return section.prefix, 1
    for section in sections:
        if f'remove_{section.prefix}' in post_data:
            return section.prefix, -1
    return None


def _pressed_sum_button(post_data, sections):
    """The section whose sum button (`sum_<prefix>`) was pressed, or None."""
    for section in sections:
        if section.sum_from and f'sum_{section.prefix}' in post_data:
            return section
    return None


def _rows_total(rows, field):
    """The total of `field` across raw POST `rows`, or None if none is readable.

    Unparsable values are skipped, since the rebuilt form is unbound and the
    real submission reports them.
    """
    total = None
    for row in rows:
        try:
            value = Decimal(row.get(field, '').strip())
        except InvalidOperation:
            continue
        if value.is_finite():
            total = value + (total or 0)
    return total


def _summed_rows(rows, section, source_rows):
    """`rows` with its single row's `sum_field` set to the total of `source_rows`.

    Only a section of exactly one row is filled; the button is hidden otherwise.
    Nothing typed leaves the row as it was.
    """
    if len(rows) != 1:
        return rows
    total = _rows_total(source_rows, section.sum_field)
    if total is None:
        return rows
    # 'f', or a sum like 1E+3 would come back in a form <input type="number"> refuses.
    return [{**rows[0], section.sum_field: format(total, 'f')}]


def _mirrored_rows(rows, section, source_rows):
    """`rows` with each blank `mirror_field` set to the `mirror_source` total of `source_rows`.

    Only rows that name their catalog record (a machine) are filled, since a
    number on an otherwise blank row is refused. A typed value is kept.
    """
    total = _rows_total(source_rows, section.mirror_source)
    if total is None:
        return rows
    picker = section.formset_class.unique_field
    return [
        {**row, section.mirror_field: format(total, 'f')}
        if row.get(picker) and not row.get(section.mirror_field, '').strip()
        else row
        for row in rows
    ]


def _submitted_rows(post_data, section):
    """One section's rows as the raw POST strings, for the unbound rebuild."""
    prefix = section.prefix
    try:
        count = int(post_data.get(f'{prefix}-TOTAL_FORMS', 0))
    except (TypeError, ValueError):
        count = 0
    return [
        {name: post_data.get(f'{prefix}-{index}-{name}', '') for name in section.row_form.base_fields}
        for index in range(min(count, MAX_ROWS_PER_SECTION))
    ]


def _submitted_author(post_data, submitter):
    """Who „Zapsat za" names in this POST, else the submitter.

    Needed before the formsets are built, since the author shapes the
    collaborator list. Errors are ignored here; the page's own form reports them.
    """
    form = WorkOrderForm(post_data, user=submitter)
    form.is_valid()
    return form.author_or(submitter)


def _resized_section(rows, delta):
    """Apply a row button: one blank row more, or the last row less.

    Returns `(rows, blank_rows)`, never fewer than `MIN_ROWS_PER_SECTION` in total.
    """
    if delta > 0:
        blank_rows = 1
    else:
        if delta < 0 and len(rows) > MIN_ROWS_PER_SECTION:
            rows = rows[:-1]
        blank_rows = 0
    return rows, max(blank_rows, MIN_ROWS_PER_SECTION - len(rows))


def _section_form_kwargs(prefix, *, author, viewer, keep=None):
    """Row-form kwargs: the author/viewer pair for workers, `keep` for the catalog sections."""
    if prefix == 'workers':
        return {'user': author, 'viewer': viewer}
    return {'keep': (keep or {}).get(prefix, ())}


def _entry_forms(request, *, form_class, sections, order_kwargs, form_kwargs, initial=None):
    """Every form on an entry page (a job or a refuelling). Returns `(order_form, formsets_by_prefix, submitted)`.

    - Row button: rebuilt *unbound* from the POST with that section resized, so
      a half-filled form comes back without errors.
    - Sum button: rebuilt the same way, with that section's single row filled
      in from its `sum_from` section (`_summed_rows`).
    - Either way, a blank `mirror_field` is filled from its `mirror_from`
      section's total (`_mirrored_rows`), as job_rows.js does live.
    - Any other POST: bound, and `submitted` is True.
    - GET: fresh forms, pre-filled from `initial` (keyed `order` and by section prefix).

    `form_kwargs` holds each section's row-form kwargs, keyed by prefix.
    """
    pressed = _pressed_row_button(request.POST, sections) if request.method == 'POST' else None
    summed = _pressed_sum_button(request.POST, sections) if request.method == 'POST' else None
    if pressed or summed:
        pressed_prefix, delta = pressed or (None, 0)
        order_form = form_class(**order_kwargs)
        # Read off the form's own fields, so „Zapsat za" is carried when present;
        # every value of a multi-value one, so a ticked „Odebrat" stays ticked.
        order_form.initial = {
            name: request.POST.getlist(name)
            if getattr(field.widget, 'allow_multiple_selected', False)
            else request.POST.get(name, '')
            for name, field in order_form.fields.items()
        }
        rebuilt = {}
        for section in sections:
            rows, blank_rows = _resized_section(
                _submitted_rows(request.POST, section),
                delta if section.prefix == pressed_prefix else 0,
            )
            if section is summed:
                source = next(other for other in sections if other.prefix == section.sum_from)
                rows = _summed_rows(rows, section, _submitted_rows(request.POST, source))
            rebuilt[section.prefix] = rows, blank_rows
        formsets = {}
        for section in sections:
            rows, blank_rows = rebuilt[section.prefix]
            if section.mirror_from:
                # From the rebuilt rows, so a summed consumed row reaches the machines too.
                rows = _mirrored_rows(rows, section, rebuilt[section.mirror_from][0])
            formsets[section.prefix] = job_row_formset(
                section, rows=rows, blank_rows=blank_rows, form_kwargs=form_kwargs[section.prefix]
            )
        return order_form, formsets, False
    if request.method == 'POST':
        # request.FILES for „Fotky"; the rows carry no upload of their own.
        order_form = form_class(request.POST, request.FILES, **order_kwargs)
        formsets = {
            section.prefix: job_row_formset(section, data=request.POST, form_kwargs=form_kwargs[section.prefix])
            for section in sections
        }
        return order_form, formsets, True
    initial = initial or {}
    order_form = form_class(initial=initial.get('order'), **order_kwargs)
    formsets = {
        section.prefix: job_row_formset(
            section, rows=initial.get(section.prefix, ()), form_kwargs=form_kwargs[section.prefix]
        )
        for section in sections
    }
    return order_form, formsets, False


def _job_forms(request, *, author, viewer, order_form_user=None, keep=None, initial=None, photos=None):
    """The job page's forms (see `_entry_forms`).

    `keep` is keyed by section prefix, plus `location` for the job form's own picker.
    `photos` is the job's stored photos on `job_edit`, which offers to remove them.
    """
    keep = keep or {}
    return _entry_forms(
        request,
        form_class=WorkOrderForm,
        sections=JOB_SECTIONS,
        order_kwargs={'user': order_form_user, 'keep_location': keep.get('location', ()), 'photos': photos},
        form_kwargs={
            section.prefix: _section_form_kwargs(section.prefix, author=author, viewer=viewer, keep=keep)
            for section in JOB_SECTIONS
        },
        initial=initial,
    )


def _forms_valid(request, order_form, formsets):
    """True if every form on the page validates; otherwise puts up the error banner."""
    if order_form.is_valid() and all(formset.is_valid() for formset in formsets.values()):
        return True
    messages.error(request, _form_error_summary(order_form, formsets))
    return False


def _valid_job_rows(request, order_form, formsets):
    """The submitted rows, or None if any form is invalid or the job doesn't balance.

    The balance error has no field, so it goes on `messages`, and so does a
    banner for any field error (`_form_error_summary`). Valid rows are
    balanced even when a job field failed, so both errors show at once.
    """
    order_valid = order_form.is_valid()
    rows_valid = all(formset.is_valid() for formset in formsets.values())
    if not (order_valid and rows_valid):
        messages.error(request, _form_error_summary(order_form, formsets))
    if not rows_valid:
        return None
    rows = _collect_rows(formsets)
    balance_error = _balance_error(rows.consumed, rows.produced)
    if balance_error:
        messages.error(request, balance_error)
    return rows if order_valid and not balance_error else None


def _entry_context(order_form, formsets, sections, **extra):
    """An entry page's context: its form, `sections` in render order, and each formset by name.

    The row bounds are rendered so `job_rows.js` reads them instead of restating them.
    """
    return {
        'order_form': order_form,
        'sections': [(section, formsets[section.prefix]) for section in sections],
        'min_rows': MIN_ROWS_PER_SECTION,
        'max_rows': MAX_ROWS_PER_SECTION,
        **{section.context_name: formsets[section.prefix] for section in sections},
        **extra,
    }


def _job_context(order_form, formsets, **extra):
    return _entry_context(order_form, formsets, JOB_SECTIONS, photo_max_count=PHOTO_MAX_COUNT, **extra)


def _review_on_submit(submitter):
    """A new entry's review fields. The submitter decides: a manager's entry,
    even one typed for a worker, is approved at once; a worker's waits."""
    if submitter.is_manager_or_admin:
        return {'status': WorkOrder.Status.APPROVED, 'reviewed_at': timezone.now(), 'reviewed_by': submitter}
    return {'status': WorkOrder.Status.PENDING}


def _approve(entry, reviewer):
    """Approve a job or a refuelling."""
    entry.status = entry.Status.APPROVED
    entry.reviewed_at = timezone.now()
    entry.reviewed_by = reviewer
    entry.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])


@login_required
def transform_create(request):
    # The author shapes the collaborator list, so settle it before any formset.
    author = _submitted_author(request.POST, request.user) if request.method == 'POST' else request.user
    order_form, formsets, submitted = _job_forms(
        request, author=author, viewer=request.user, order_form_user=request.user
    )
    if submitted:
        rows = _valid_job_rows(request, order_form, formsets)
        if rows is not None:
            review = _review_on_submit(request.user)
            with transaction.atomic():
                work_order = WorkOrder.objects.create(
                    created_by=author,
                    location=order_form.cleaned_data['location'],
                    description=order_form.cleaned_data['description'],
                    notes=order_form.cleaned_data['notes'],
                    performed_on=order_form.cleaned_data['performed_on'],
                    **review,
                )
                _write_job_rows(work_order, author, order_form.cleaned_data['hours'], rows)
                _apply_photos(work_order, order_form.cleaned_data)
            messages.success(
                request,
                'Zpracování bylo zaznamenáno.'
                if review['status'] == WorkOrder.Status.APPROVED
                else 'Zpracování bylo zaznamenáno a čeká na schválení vedoucím.',
            )
            return redirect('transform_create')
    return render(request, 'workorders/transform_form.html', _job_context(order_form, formsets))


def _trim(value):
    """A stored Decimal without trailing zeros, so the stricter form accepts it as `initial`.

    Avoids normalize() turning 100.00 into 1E+2.
    """
    normalized = value.normalize()
    return normalized.quantize(Decimal(1)) if normalized.as_tuple().exponent > 0 else normalized


@role_required(*REVIEWER_ROLES)
def protected_media(request, path):
    """Serve an uploaded job photo (MEDIA_URL) behind `job_detail`'s gate.

    MEDIA_ROOT is never served by the proxy or whitenoise, which would make the
    photos public. The gate assumes job photos are all that live there.
    `FileSystemStorage` rejects path traversal itself (`safe_join`).
    """
    try:
        stored = default_storage.open(path)
    except OSError:
        # Missing, or a directory.
        raise Http404 from None
    # Inline, and typed off the name, so `job_photo`'s <img> can show it.
    return FileResponse(stored, filename=path.rsplit('/', 1)[-1])


@role_required(*REVIEWER_ROLES)
def job_dashboard(request):
    """Every recorded job, newest first. Manager-only, so not scoped per worker."""
    form = JobFilterForm(request.GET or None)
    jobs = (
        WorkOrder.objects.select_related('created_by', 'reviewed_by', 'location')
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
            'pending_refuel_count': Refuelling.objects.filter(status=Refuelling.Status.PENDING).count(),
            **_list_page_context(request, jobs),
        },
    )


def _job_line_items(work_order):
    """The job's consumed and produced rows, quantities as they were typed."""
    consumed, produced = [], []
    for movement in work_order.movements.select_related('material'):
        movement.typed_quantity = abs(movement.quantity)
        if movement.movement_type == StockMovement.MovementType.TRANSFORM_CONSUME:
            consumed.append(movement)
        else:
            produced.append(movement)
    return consumed, produced


@role_required(*REVIEWER_ROLES)
def job_detail(request, pk):
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by', 'reviewed_by', 'location'), pk=pk)
    consumed, produced = _job_line_items(work_order)
    return render(
        request,
        'workorders/job_detail.html',
        {
            'work_order': work_order,
            'photos': work_order.photos.all(),
            'consumed': consumed,
            'produced': produced,
            'machine_usages': work_order.machine_usages.select_related('machine'),
            'worker_hours': work_order.worker_hours.select_related('user'),
        },
    )


@role_required(*REVIEWER_ROLES)
def job_photo(request, pk, number):
    """„Fotka <number>" of a job, inside the app's own page.

    A link straight to the file strands an installed app (no address bar, and
    no back gesture on an iPhone), so the job pages link here instead.
    `number` is the 1-based position the job pages label it with.
    """
    work_order = get_object_or_404(WorkOrder, pk=pk)
    photos = list(work_order.photos.all())
    if not 1 <= number <= len(photos):
        raise Http404
    return render(
        request,
        'workorders/job_photo.html',
        {
            'work_order': work_order,
            'photo': photos[number - 1],
            'number': number,
            'count': len(photos),
            'previous_number': number - 1 if number > 1 else None,
            'next_number': number + 1 if number < len(photos) else None,
        },
    )


@role_required(*REVIEWER_ROLES)
def job_edit(request, pk):
    """Correct a recorded job. Does not approve it."""
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by'), pk=pk)
    # The rows stay the author's, not the editing manager's.
    author = work_order.created_by
    consumed, produced = _job_line_items(work_order)
    usages = list(work_order.machine_usages.all())
    # Keep retired records the job names selectable; see `_offer_recorded`.
    keep = {
        'consumed': [movement.material_id for movement in consumed],
        'produced': [movement.material_id for movement in produced],
        'machines': [usage.machine_id for usage in usages],
        'location': [work_order.location_id] if work_order.location_id else [],
    }
    order_form, formsets, submitted = _job_forms(
        request,
        author=author,
        viewer=request.user,
        keep=keep,
        initial=_edit_initial(work_order, author, consumed, produced, usages) if request.method == 'GET' else None,
        photos=work_order.photos.all(),
    )
    if submitted:
        rows = _valid_job_rows(request, order_form, formsets)
        if rows is not None:
            with transaction.atomic():
                work_order.location = order_form.cleaned_data['location']
                work_order.description = order_form.cleaned_data['description']
                work_order.notes = order_form.cleaned_data['notes']
                work_order.performed_on = order_form.cleaned_data['performed_on']
                work_order.save(update_fields=['location', 'description', 'notes', 'performed_on'])
                _write_job_rows(work_order, author, order_form.cleaned_data['hours'], rows)
                _apply_photos(work_order, order_form.cleaned_data)
            messages.success(request, 'Zpracování bylo upraveno.')
            return redirect('job_detail', pk=work_order.pk)
    # Set on every path, since an invalid POST re-renders too.
    order_form.fields['hours'].label = f'Hodiny – {author.username}'
    return render(request, 'workorders/job_edit.html', _job_context(order_form, formsets, work_order=work_order))


def _trimmed(row):
    """A row's Decimals passed through `_trim`; None (unknown) stays None."""
    return {key: _trim(value) if isinstance(value, Decimal) else value for key, value in row.items()}


def _edit_initial(work_order, author, consumed, produced, usages):
    """`job_edit`'s pre-fill: the job as recorded, quantities as they were typed."""
    own_hours = work_order.worker_hours.filter(user=author).first()
    return {
        'order': {
            'location': work_order.location_id,
            'description': work_order.description,
            'notes': work_order.notes,
            'hours': _trim(own_hours.hours) if own_hours else None,
            'performed_on': work_order.performed_on,
        },
        'consumed': [{'material': m.material_id, 'quantity': _trim(m.typed_quantity)} for m in consumed],
        'produced': [{'material': m.material_id, 'quantity': _trim(m.typed_quantity)} for m in produced],
        'machines': [
            _trimmed({'machine': usage.machine_id, 'hours': usage.hours, 'tons': usage.tons}) for usage in usages
        ],
        'workers': [
            {'user': row.user_id, 'hours': _trim(row.hours)} for row in work_order.worker_hours.exclude(user=author)
        ],
    }


@role_required(*REVIEWER_ROLES)
@require_POST
def job_approve(request, pk):
    work_order = get_object_or_404(WorkOrder, pk=pk)
    _approve(work_order, request.user)
    messages.success(request, 'Zpracování bylo schváleno.')
    return redirect('job_detail', pk=work_order.pk)


@role_required(*REVIEWER_ROLES)
def job_delete(request, pk):
    work_order = get_object_or_404(WorkOrder.objects.select_related('created_by'), pk=pk)
    if request.method == 'POST':
        # Every row on the job cascades.
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
