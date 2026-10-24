"""Recording and correcting an entry of either kind (`kinds.Kind`), approving
it, the job's own review pages (Přehled and the job pages), and serving the
photos attached to a job. The refuelling's own pages are in `refuels.py`."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db.models import Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import REVIEWER_ROLES, role_required

from .forms import JobFilterForm
from .kinds import JOB, _approve
from .models import JobPhoto, Refuelling, StockMovement, WorkOrder
from .reports import _list_page_context, _previews


@login_required
def entry_create(request, kind):
    """Record a new entry of `kind`: Zpracování or Tankování."""
    page = kind.page(request)
    if page.submitted and kind.is_valid(page):
        entry = kind.save(page)
        messages.success(request, kind.saved_message if entry.is_approved else kind.pending_message)
        return redirect(kind.create_url)
    return render(request, kind.create_template, page.context(**kind.extra_context(request, None)))


@login_required
def entry_edit(request, pk, kind):
    """Correct a recorded entry of `kind`. Does not approve it.

    A reviewer's page, which `kind.may_edit` also opens to the author of a
    pending job. They go back to the form, since the detail page is a reviewer's.
    """
    entry = get_object_or_404(kind.model.objects.select_related('created_by'), pk=pk)
    if not kind.may_edit(request.user, entry):
        raise PermissionDenied
    page = kind.page(request, entry)
    if page.submitted and kind.is_valid(page):
        kind.save(page, entry)
        messages.success(request, kind.edited_message)
        if request.user.is_manager_or_admin:
            return redirect(kind.detail_url, pk=entry.pk)
        return redirect(kind.create_url)
    return render(
        request,
        kind.edit_template,
        page.context(**{kind.context_name: entry}, **kind.extra_context(request, entry)),
    )


@role_required(*REVIEWER_ROLES)
@require_POST
def entry_approve(request, pk, kind):
    """Approve the entry as the reviewer read it: the detail page posts the `revision` it showed."""
    entry = get_object_or_404(kind.model, pk=pk)
    try:
        revision = int(request.POST.get('revision', ''))
    except ValueError:
        revision = None
    if entry.is_approved:
        # Approved by someone else meanwhile; it is approved either way.
        messages.success(request, kind.approved_message)
    elif revision is not None and _approve(entry, revision, request.user):
        messages.success(request, kind.approved_message)
    else:
        messages.error(request, kind.changed_message)
    return redirect(kind.detail_url, pk=entry.pk)


@login_required
def protected_media(request, path):
    """Serve an uploaded job photo (MEDIA_URL) behind `job_detail`'s gate, or `job_edit`'s for its author.

    MEDIA_ROOT is never served by the proxy or whitenoise, which would make the
    photos public. The gate assumes job photos are all that live there.
    `FileSystemStorage` rejects path traversal itself (`safe_join`).
    """
    if not request.user.is_manager_or_admin:
        photo = JobPhoto.objects.select_related('work_order').filter(file=path).first()
        # 403 for a missing file too, so a worker can't probe what exists.
        if photo is None or not JOB.may_edit(request.user, photo.work_order):
            raise PermissionDenied
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
            'tables': _previews(request, jobs=listing['rows']),
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


@login_required
def job_photo(request, pk, number):
    """„Fotka <number>" of a job, inside the app's own page.

    A link straight to the file strands an installed app (no address bar, and
    no back gesture on an iPhone), so the job pages link here instead.
    `number` is the 1-based position the job pages label it with. Gated like
    `job_edit`, which links here for a worker correcting their pending job.
    """
    work_order = get_object_or_404(WorkOrder, pk=pk)
    if not JOB.may_edit(request.user, work_order):
        raise PermissionDenied
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
