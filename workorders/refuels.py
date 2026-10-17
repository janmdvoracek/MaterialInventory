"""The refuelling's own review pages (Přehled tankování, detail, delete) and a
worker's recent refuellings.

A refuelling is its own kind of entry, apart from any job: notes, a date, and
one row per machine (stroj, zdroj, litry). It is recorded, corrected and
approved through the generic entry views with `kinds.REFUELLING`, and only
approved ones count on Stroje.
"""

from django.contrib import messages
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import REVIEWER_ROLES, role_required

from .forms import RefuellingFilterForm
from .models import Refuelling
from .reports import MY_JOBS_LIMIT, _list_page_context, _previews


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
