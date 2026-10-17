"""Recording a refuelling (Tankování) and reviewing it.

A refuelling is its own kind of entry, apart from any job: notes, a date, and
one row per machine (stroj, zdroj, litry). It is reviewed like a job, and only
approved ones count on Stroje. The form machinery is the job form's
(`entry.EntryPage`), with `REFUEL_SECTIONS` as its one section.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import REVIEWER_ROLES, role_required

from .entry import EntryPage
from .forms import RefuellingFilterForm, RefuellingForm
from .models import Refuelling
from .reports import MY_JOBS_LIMIT, _list_page_context, _previews
from .sections import REFUEL_SECTIONS
from .views import _approve, _review_on_submit

NO_ROWS_ERROR = 'Přidejte alespoň jeden natankovaný stroj.'


def _refuel_forms(request, *, refuelling=None, order_initial=None):
    """The refuelling page's `EntryPage`; `refuelling` is the one being edited, if any."""
    return EntryPage(
        request,
        form_class=RefuellingForm,
        sections=REFUEL_SECTIONS,
        order_kwargs={},
        author=refuelling.created_by if refuelling is not None else request.user,
        viewer=request.user,
        entry=refuelling,
        order_initial=order_initial,
    )


def _refuelling_is_valid(request, page):
    """True if every form validates and at least one machine was named."""
    if not page.is_valid():
        return False
    if not page.filled_rows('refuels'):
        messages.error(request, NO_ROWS_ERROR)
        return False
    return True


def _with_totals(refuellings):
    """Each entry with its `total_litres` and `machine_count`."""
    return refuellings.annotate(total_litres=Sum('machine_refuels__litres'), machine_count=Count('machine_refuels'))


def _my_recent_refuellings(user):
    """A worker's latest refuellings with their status — the only review feedback they get.

    Like Souhrn's `_my_recent_jobs`: by entry time, and empty for managers.
    """
    if user.is_manager_or_admin:
        return Refuelling.objects.none()
    return _with_totals(Refuelling.objects.filter(created_by=user)).order_by('-created_at')[:MY_JOBS_LIMIT]


@login_required
def refuel_create(request):
    page = _refuel_forms(request)
    order_form = page.order_form
    if page.submitted and _refuelling_is_valid(request, page):
        review = _review_on_submit(request.user)
        with transaction.atomic():
            refuelling = Refuelling.objects.create(
                created_by=request.user,
                notes=order_form.cleaned_data['notes'],
                performed_on=order_form.cleaned_data['performed_on'],
                **review,
            )
            page.write_rows(refuelling, request.user)
        messages.success(
            request,
            'Tankování bylo zaznamenáno.'
            if review['status'] == Refuelling.Status.APPROVED
            else 'Tankování bylo zaznamenáno a čeká na schválení vedoucím.',
        )
        return redirect('refuel_create')
    return render(
        request,
        'workorders/refuel_form.html',
        page.context(my_refuellings=_my_recent_refuellings(request.user)),
    )


@role_required(*REVIEWER_ROLES)
def refuel_dashboard(request):
    """Every recorded refuelling, newest first. Manager-only, so not scoped per worker."""
    form = RefuellingFilterForm(request.GET or None)
    refuellings = (
        _with_totals(Refuelling.objects.select_related('created_by'))
        .prefetch_related('machine_refuels__machine')
        .order_by('-performed_on', '-created_at')
    )
    listing = _list_page_context(request, form.filter(refuellings))
    return render(
        request,
        'workorders/refuel_dashboard.html',
        {
            'form': form,
            'pending_count': Refuelling.objects.filter(status=Refuelling.Status.PENDING).count(),
            'tables': _previews(request, refuellings=listing),
            **listing,
        },
    )


def _fill_ups(refuelling):
    return refuelling.machine_refuels.select_related('machine', 'source')


@role_required(*REVIEWER_ROLES)
def refuel_detail(request, pk):
    refuelling = get_object_or_404(Refuelling.objects.select_related('created_by', 'reviewed_by'), pk=pk)
    return render(
        request,
        'workorders/refuel_detail.html',
        {'refuelling': refuelling, 'fill_ups': _fill_ups(refuelling)},
    )


@role_required(*REVIEWER_ROLES)
def refuel_edit(request, pk):
    """Correct a recorded refuelling. Does not approve it."""
    refuelling = get_object_or_404(Refuelling.objects.select_related('created_by'), pk=pk)
    initial = {'notes': refuelling.notes, 'performed_on': refuelling.performed_on}
    page = _refuel_forms(request, refuelling=refuelling, order_initial=initial if request.method == 'GET' else None)
    order_form = page.order_form
    if page.submitted and _refuelling_is_valid(request, page):
        with transaction.atomic():
            refuelling.notes = order_form.cleaned_data['notes']
            refuelling.performed_on = order_form.cleaned_data['performed_on']
            refuelling.save(update_fields=['notes', 'performed_on'])
            page.write_rows(refuelling, refuelling.created_by)
        messages.success(request, 'Tankování bylo upraveno.')
        return redirect('refuel_detail', pk=refuelling.pk)
    return render(
        request,
        'workorders/refuel_edit.html',
        page.context(refuelling=refuelling),
    )


@role_required(*REVIEWER_ROLES)
@require_POST
def refuel_approve(request, pk):
    refuelling = get_object_or_404(Refuelling, pk=pk)
    _approve(refuelling, request.user)
    messages.success(request, 'Tankování bylo schváleno.')
    return redirect('refuel_detail', pk=refuelling.pk)


@role_required(*REVIEWER_ROLES)
def refuel_delete(request, pk):
    refuelling = get_object_or_404(Refuelling.objects.select_related('created_by'), pk=pk)
    if request.method == 'POST':
        # Its fill-ups cascade.
        refuelling.delete()
        messages.success(request, 'Tankování bylo smazáno.')
        return redirect('refuel_dashboard')
    return render(
        request,
        'workorders/refuel_confirm_delete.html',
        {'refuelling': refuelling, 'fill_ups': _fill_ups(refuelling)},
    )
