"""Recording a refuelling (Tankování) and reviewing it.

A refuelling is its own kind of entry, apart from any job: notes, a date, and
one row per machine (stroj, zdroj, litry). It is reviewed like a job, and only
approved ones count on Stroje. The form machinery is the job form's
(`views._entry_forms`), with `REFUEL_SECTIONS` as its one section.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import REVIEWER_ROLES, role_required

from .forms import REFUEL_SECTIONS, RefuellingFilterForm, RefuellingForm
from .models import MachineRefuel, Refuelling
from .reports import MY_JOBS_LIMIT, _list_page_context, _previews
from .views import _approve, _entry_context, _entry_forms, _filled_rows, _forms_valid, _review_on_submit, _trimmed

NO_ROWS_ERROR = 'Přidejte alespoň jeden natankovaný stroj.'


def _refuel_forms(request, *, keep=None, initial=None):
    """The refuelling page's forms (see `_entry_forms`). `keep` holds `machines` and `sources` on `refuel_edit`."""
    keep = keep or {}
    return _entry_forms(
        request,
        form_class=RefuellingForm,
        sections=REFUEL_SECTIONS,
        order_kwargs={},
        form_kwargs={'refuels': {'keep': keep.get('machines', ()), 'keep_source': keep.get('sources', ())}},
        initial=initial,
    )


def _valid_refuel_rows(request, order_form, formsets):
    """The filled-in rows, or None if any form is invalid or no machine was named."""
    if not _forms_valid(request, order_form, formsets):
        return None
    rows = _filled_rows(formsets['refuels'], 'machine')
    if not rows:
        messages.error(request, NO_ROWS_ERROR)
        return None
    return rows


def _write_refuel_rows(refuelling, rows):
    """Replace the entry's fill-ups with the submitted rows. Call inside `transaction.atomic()`."""
    refuelling.machine_refuels.all().delete()
    MachineRefuel.objects.bulk_create(
        MachineRefuel(refuelling=refuelling, machine=row['machine'], source=row['source'], litres=row['litres'])
        for row in rows
    )


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
    order_form, formsets, submitted = _refuel_forms(request)
    if submitted:
        rows = _valid_refuel_rows(request, order_form, formsets)
        if rows is not None:
            review = _review_on_submit(request.user)
            with transaction.atomic():
                refuelling = Refuelling.objects.create(
                    created_by=request.user,
                    notes=order_form.cleaned_data['notes'],
                    performed_on=order_form.cleaned_data['performed_on'],
                    **review,
                )
                _write_refuel_rows(refuelling, rows)
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
        _entry_context(order_form, formsets, REFUEL_SECTIONS, my_refuellings=_my_recent_refuellings(request.user)),
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
    fill_ups = list(refuelling.machine_refuels.all())
    # Keep retired machines and sources the entry names selectable; see `_offer_recorded`.
    keep = {
        'machines': [fill_up.machine_id for fill_up in fill_ups],
        'sources': [fill_up.source_id for fill_up in fill_ups if fill_up.source_id],
    }
    initial = None
    if request.method == 'GET':
        initial = {
            'order': {'notes': refuelling.notes, 'performed_on': refuelling.performed_on},
            'refuels': [
                _trimmed({'machine': fill_up.machine_id, 'source': fill_up.source_id, 'litres': fill_up.litres})
                for fill_up in fill_ups
            ],
        }
    order_form, formsets, submitted = _refuel_forms(request, keep=keep, initial=initial)
    if submitted:
        rows = _valid_refuel_rows(request, order_form, formsets)
        if rows is not None:
            with transaction.atomic():
                refuelling.notes = order_form.cleaned_data['notes']
                refuelling.performed_on = order_form.cleaned_data['performed_on']
                refuelling.save(update_fields=['notes', 'performed_on'])
                _write_refuel_rows(refuelling, rows)
            messages.success(request, 'Tankování bylo upraveno.')
            return redirect('refuel_detail', pk=refuelling.pk)
    return render(
        request,
        'workorders/refuel_edit.html',
        _entry_context(order_form, formsets, REFUEL_SECTIONS, refuelling=refuelling),
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
