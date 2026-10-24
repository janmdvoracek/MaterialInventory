"""The entry kinds: what a job and a refuelling each *are* on their entry page.

A kind declares its model, form, sections, own fields, templates, URLs and
messages, and overrides a hook only where it differs (author, form kwargs,
edit pre-fill, page-level rule, extra rows, extra context). The generic views
(`views.entry_create`, `entry_edit`, `entry_approve`) drive a kind through
`page`, `is_valid` and `save`; `entry.EntryPage` stays the form machinery.
"""

from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.formats import localize

from .entry import EntryPage
from .forms import PHOTO_MAX_COUNT, RefuellingForm, WorkOrderForm
from .models import JobPhoto, Refuelling, ReviewStatus, WorkerHours, WorkOrder
from .refuels import _my_recent_refuellings
from .sections import JOB_SECTIONS, REFUEL_SECTIONS, _trim

NO_ROWS_ERROR = 'Přidejte alespoň jeden natankovaný stroj.'


def _review_on_submit(submitter):
    """A new entry's review fields. The submitter decides: a manager's entry,
    even one typed for a worker, is approved at once; a worker's waits."""
    if submitter.is_manager_or_admin:
        return {'status': ReviewStatus.APPROVED, 'reviewed_at': timezone.now(), 'reviewed_by': submitter}
    return {'status': ReviewStatus.PENDING}


def _approve(entry, revision, reviewer):
    """Approve a job or a refuelling, if it is still pending at the `revision` the reviewer read.

    One conditional UPDATE, so an edit saved after the reviewer opened the
    page can't be approved unseen. Returns whether it approved.
    """
    return bool(
        type(entry)
        .objects.filter(pk=entry.pk, revision=revision, status=ReviewStatus.PENDING)
        .update(status=ReviewStatus.APPROVED, reviewed_at=timezone.now(), reviewed_by=reviewer)
    )


class Kind:
    """One kind of entry. Subclasses set the declarations and override hooks where they differ.

    `own_fields` are the cleaned fields copied onto the entry on create and on
    edit. `context_name` is what the edit template calls the entry.
    `author_edits_pending` lets a worker correct their own entry until it is approved.
    """

    model = None
    form_class = None
    sections = ()
    own_fields = ()
    create_template = ''
    edit_template = ''
    context_name = ''
    create_url = ''
    detail_url = ''
    saved_message = ''
    pending_message = ''
    edited_message = ''
    approved_message = ''
    changed_message = ''
    author_edits_pending = False

    def may_edit(self, user, entry):
        """A reviewer edits any entry; with `author_edits_pending`, its author edits it while it is pending."""
        if user.is_manager_or_admin:
            return True
        return self.author_edits_pending and entry.created_by_id == user.pk and not entry.is_approved

    def page(self, request, entry=None):
        """The entry page for a new entry, or for correcting `entry`."""
        author = self.author(request, entry)
        editing_get = entry is not None and request.method == 'GET'
        return EntryPage(
            request,
            form_class=self.form_class,
            sections=self.sections,
            order_kwargs=self.form_kwargs(request, entry),
            author=author,
            viewer=request.user,
            entry=entry,
            order_initial=self.edit_initial(entry, author) if editing_get else None,
        )

    def is_valid(self, page):
        """True if every form validates and `page_errors` finds nothing.

        The page-level errors have no field, so they go on `messages`, beside
        the banner `page.is_valid()` puts up for any field error. Valid rows
        are checked even when an entry field failed, so both errors show.
        """
        valid = page.is_valid()
        if not page.rows_valid():
            return False
        errors = self.page_errors(page)
        for error in errors:
            messages.error(page.request, error)
        return valid and not errors

    def save(self, page, entry=None):
        """Create the entry (review on submit) or update `entry`, and write its rows, in one transaction.

        An edit locks the row and asks `may_edit` again, so an author's
        correction can't land after an approval; it raises the `revision`, so
        an approval of the version before it is refused.
        """
        own = {name: page.order_form.cleaned_data[name] for name in self.own_fields}
        with transaction.atomic():
            if entry is None:
                entry = self.model.objects.create(created_by=page.author, **own, **_review_on_submit(page.request.user))
            else:
                locked = self.model.objects.select_for_update().get(pk=entry.pk)
                if not self.may_edit(page.request.user, locked):
                    raise PermissionDenied
                for name, value in own.items():
                    setattr(entry, name, value)
                entry.revision = locked.revision + 1
                entry.save(update_fields=[*self.own_fields, 'revision'])
            self.write_rows(entry, page)
        return entry

    def author(self, request, entry):
        """Whose entry this is. The rows stay the author's, not the editing manager's."""
        return entry.created_by if entry is not None else request.user

    def form_kwargs(self, request, entry):
        return {}

    def edit_initial(self, entry, author):
        """The edit page's pre-fill of the entry's own fields; the sections fill their own rows."""
        return {name: getattr(entry, name) for name in self.own_fields}

    def page_errors(self, page):
        """The errors only the whole page can show, once every row is valid."""
        return []

    def write_rows(self, entry, page):
        """Store every section's rows. Called inside `save`'s transaction."""
        page.write_rows(entry)

    def extra_context(self, request, entry):
        return {}


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


def _my_pending_jobs(user):
    """The user's own jobs awaiting approval, oldest first: the ones `JobKind.may_edit` lets them correct."""
    return WorkOrder.objects.filter(created_by=user, status=WorkOrder.Status.PENDING).order_by('created_at')


def _submitted_author(post_data, submitter):
    """Who „Zapsat za" names in this POST, else the submitter.

    Needed before the formsets are built, since the author shapes the
    collaborator list. Errors are ignored here; the page's own form reports them.
    """
    form = WorkOrderForm(post_data, user=submitter)
    form.is_valid()
    return form.author_or(submitter)


class JobKind(Kind):
    """A processing job (Zpracování): mass-balanced, with „Zapsat za", own hours and photos."""

    model = WorkOrder
    form_class = WorkOrderForm
    sections = JOB_SECTIONS
    own_fields = ('location', 'description', 'notes', 'performed_on')
    create_template = 'workorders/transform_form.html'
    edit_template = 'workorders/job_edit.html'
    context_name = 'work_order'
    create_url = 'transform_create'
    detail_url = 'job_detail'
    saved_message = 'Zpracování bylo zaznamenáno.'
    pending_message = 'Zpracování bylo zaznamenáno a čeká na schválení vedoucím.'
    edited_message = 'Zpracování bylo upraveno.'
    approved_message = 'Zpracování bylo schváleno.'
    changed_message = 'Zpracování se mezitím změnilo, proto nebylo schváleno. Zkontrolujte ho prosím znovu.'
    author_edits_pending = True

    def page(self, request, entry=None):
        page = super().page(request, entry)
        if entry is not None:
            page.order_form.fields['hours'].label = f'Hodiny – {page.author.username}'
        return page

    def author(self, request, entry):
        # The author shapes the collaborator list, so settle it before any formset.
        if entry is None and request.method == 'POST':
            return _submitted_author(request.POST, request.user)
        return super().author(request, entry)

    def form_kwargs(self, request, entry):
        """„Zapsat za" on a new job only; on edit, the job's own location stays
        pickable even if retired (see `_offer_recorded`), and its stored photos
        are offered for removal."""
        if entry is None:
            return {'user': request.user, 'keep_location': [], 'photos': None}
        return {
            'user': None,
            'keep_location': [entry.location_id] if entry.location_id else [],
            'photos': entry.photos.all(),
        }

    def edit_initial(self, entry, author):
        own_hours = entry.worker_hours.filter(user=author).first()
        return {
            **super().edit_initial(entry, author),
            'location': entry.location_id,
            'hours': _trim(own_hours.hours) if own_hours else None,
        }

    def page_errors(self, page):
        error = _balance_error(page.filled_rows('consumed'), page.filled_rows('produced'))
        return [error] if error else []

    def write_rows(self, entry, page):
        """The author's own hours (the job's „Moje hodiny"), every section's rows, then the photos.

        The own hours come first, so they stay the job's first hours row.
        Stored photos ticked „Odebrat" are deleted (their files on commit,
        `models.discard_photo`); new photos never replace stored ones.
        """
        cleaned_data = page.order_form.cleaned_data
        WorkerHours.objects.update_or_create(
            work_order=entry, user=page.author, defaults={'hours': cleaned_data['hours']}
        )
        super().write_rows(entry, page)
        for photo in cleaned_data.get('remove_photos') or ():
            photo.delete()
        JobPhoto.objects.bulk_create(JobPhoto(work_order=entry, file=upload) for upload in cleaned_data['photo'])

    def extra_context(self, request, entry):
        if entry is None:
            # The jobs the user can still correct, as links above the form.
            return {'photo_max_count': PHOTO_MAX_COUNT, 'pending_jobs': _my_pending_jobs(request.user)}
        return {'photo_max_count': PHOTO_MAX_COUNT, 'photos': list(entry.photos.all())}


class RefuellingKind(Kind):
    """A refuelling (Tankování): notes, a date, and at least one machine."""

    model = Refuelling
    form_class = RefuellingForm
    sections = REFUEL_SECTIONS
    own_fields = ('notes', 'performed_on')
    create_template = 'workorders/refuel_form.html'
    edit_template = 'workorders/refuel_edit.html'
    context_name = 'refuelling'
    create_url = 'refuel_create'
    detail_url = 'refuel_detail'
    saved_message = 'Tankování bylo zaznamenáno.'
    pending_message = 'Tankování bylo zaznamenáno a čeká na schválení vedoucím.'
    edited_message = 'Tankování bylo upraveno.'
    approved_message = 'Tankování bylo schváleno.'
    changed_message = 'Tankování se mezitím změnilo, proto nebylo schváleno. Zkontrolujte ho prosím znovu.'

    def page_errors(self, page):
        return [] if page.filled_rows('refuels') else [NO_ROWS_ERROR]

    def extra_context(self, request, entry):
        # A worker's only review feedback, under the form.
        return {'my_refuellings': _my_recent_refuellings(request.user)} if entry is None else {}


JOB = JobKind()
REFUELLING = RefuellingKind()
