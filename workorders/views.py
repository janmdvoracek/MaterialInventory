"""Recording a job (Zpracování), reviewing it (Přehled and the job pages), and
serving the photos attached to one."""

from decimal import Decimal

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

from .entry import EntryPage
from .forms import PHOTO_MAX_COUNT, JobFilterForm, WorkOrderForm
from .models import JobPhoto, Refuelling, StockMovement, WorkerHours, WorkOrder
from .reports import _list_page_context, _previews
from .sections import JOB_SECTIONS, _trim


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


def _write_job_rows(work_order, author, own_hours, page):
    """Store the author's own hours (the job's „Moje hodiny") and every section's rows.

    Call inside `transaction.atomic()`. The rows belong to `author`, not the editor.
    The author's own hours come first, so they stay the job's first hours row.
    """
    WorkerHours.objects.update_or_create(work_order=work_order, user=author, defaults={'hours': own_hours})
    page.write_rows(work_order, author)


def _submitted_author(post_data, submitter):
    """Who „Zapsat za" names in this POST, else the submitter.

    Needed before the formsets are built, since the author shapes the
    collaborator list. Errors are ignored here; the page's own form reports them.
    """
    form = WorkOrderForm(post_data, user=submitter)
    form.is_valid()
    return form.author_or(submitter)


def _job_forms(request, *, author, viewer, order_form_user=None, work_order=None, order_initial=None):
    """The job page's `EntryPage`; `work_order` is the job being edited, if any.

    The sections read their rows off `work_order`. The job's own location is
    kept pickable even if retired (see `_offer_recorded`), and its stored photos
    are offered for removal.
    """
    keep_location = [work_order.location_id] if work_order is not None and work_order.location_id else []
    return EntryPage(
        request,
        form_class=WorkOrderForm,
        sections=JOB_SECTIONS,
        order_kwargs={
            'user': order_form_user,
            'keep_location': keep_location,
            'photos': work_order.photos.all() if work_order is not None else None,
        },
        author=author,
        viewer=viewer,
        entry=work_order,
        order_initial=order_initial,
    )


def _job_is_valid(request, page):
    """True if every form validates and the job balances.

    The balance error has no field, so it goes on `messages`, beside the banner
    `page.is_valid()` puts up for any field error. Valid rows are balanced even
    when a job field failed, so both errors show at once.
    """
    valid = page.is_valid()
    if not page.rows_valid():
        return False
    balance_error = _balance_error(page.filled_rows('consumed'), page.filled_rows('produced'))
    if balance_error:
        messages.error(request, balance_error)
    return valid and not balance_error


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
    page = _job_forms(request, author=author, viewer=request.user, order_form_user=request.user)
    order_form = page.order_form
    if page.submitted and _job_is_valid(request, page):
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
            _write_job_rows(work_order, author, order_form.cleaned_data['hours'], page)
            _apply_photos(work_order, order_form.cleaned_data)
        messages.success(
            request,
            'Zpracování bylo zaznamenáno.'
            if review['status'] == WorkOrder.Status.APPROVED
            else 'Zpracování bylo zaznamenáno a čeká na schválení vedoucím.',
        )
        return redirect('transform_create')
    return render(request, 'workorders/transform_form.html', page.context(photo_max_count=PHOTO_MAX_COUNT))


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
    listing = _list_page_context(request, form.filter(jobs))
    return render(
        request,
        'workorders/job_dashboard.html',
        {
            'form': form,
            'pending_count': WorkOrder.objects.filter(status=WorkOrder.Status.PENDING).count(),
            'pending_refuel_count': Refuelling.objects.filter(status=Refuelling.Status.PENDING).count(),
            'tables': _previews(request, jobs=listing),
            **listing,
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
    page = _job_forms(
        request,
        author=author,
        viewer=request.user,
        work_order=work_order,
        order_initial=_edit_initial(work_order, author) if request.method == 'GET' else None,
    )
    order_form = page.order_form
    if page.submitted and _job_is_valid(request, page):
        with transaction.atomic():
            work_order.location = order_form.cleaned_data['location']
            work_order.description = order_form.cleaned_data['description']
            work_order.notes = order_form.cleaned_data['notes']
            work_order.performed_on = order_form.cleaned_data['performed_on']
            work_order.save(update_fields=['location', 'description', 'notes', 'performed_on'])
            _write_job_rows(work_order, author, order_form.cleaned_data['hours'], page)
            _apply_photos(work_order, order_form.cleaned_data)
        messages.success(request, 'Zpracování bylo upraveno.')
        return redirect('job_detail', pk=work_order.pk)
    # Set on every path, since an invalid POST re-renders too.
    order_form.fields['hours'].label = f'Hodiny – {author.username}'
    return render(
        request,
        'workorders/job_edit.html',
        page.context(photo_max_count=PHOTO_MAX_COUNT, work_order=work_order, photos=list(work_order.photos.all())),
    )


def _edit_initial(work_order, author):
    """`job_edit`'s pre-fill of the job's own fields; the sections fill their own rows."""
    own_hours = work_order.worker_hours.filter(user=author).first()
    return {
        'location': work_order.location_id,
        'description': work_order.description,
        'notes': work_order.notes,
        'hours': _trim(own_hours.hours) if own_hours else None,
        'performed_on': work_order.performed_on,
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
