from dataclasses import dataclass
from decimal import Decimal

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import FileExtensionValidator
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from locations.models import Location
from machines.models import FuelSource, Machine
from materials.models import Material

from .models import ReviewStatus, StockMovement
from .photos import UnreadablePhoto, shrink_photo

# Upper bounds for the job form's number boxes, rendered as each input's `max`.
# They keep values far inside the decimal(12, 2) columns, so an oversized value
# is a field error, not a `numeric field overflow` 500. Whole numbers because
# Django prints the limit unlocalised. The admin inlines repeat them.
WORKER_HOURS_MAX = Decimal('24')  # a job is one day
METER_READING_MAX = Decimal('99999')  # five-digit hour meter
TONS_MAX = Decimal('99999')
LITRES_MAX = Decimal('2000')  # above any tank; refuses a slipped extra zero

# Form-only caps (tighter than the columns, so no migration).
# `WorkOrderAdminForm` repeats the description cap, so admin-saved jobs stay editable.
DESCRIPTION_MAX_LENGTH = 40
NOTES_MAX_LENGTH = 2000

# HEIC/HEIF is what an iPhone shoots; everything is stored as JPEG (`photos.shrink_photo`).
PHOTO_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif']

# Per upload, checked before decoding; stored files are shrunk to a few hundred KB.
PHOTO_MAX_BYTES = 10 * 1024 * 1024

# Per job, stored and new together.
PHOTO_MAX_COUNT = 10

# Per submission, checked before decoding. Keep below the Caddyfile's
# `request_body max_size` (25 MB), so a refused batch gets a Czech error, not a 413.
PHOTO_MAX_TOTAL_BYTES = 20 * 1024 * 1024


def clean_photo_upload(photo):
    """Refuse an oversized or undecodable upload, and shrink the rest.

    Shared by `WorkOrderForm` and the admin's photo inline. A FieldFile (a
    stored photo passing back through the admin) is returned untouched.
    """
    if not isinstance(photo, UploadedFile):
        return photo
    if photo.size > PHOTO_MAX_BYTES:
        raise forms.ValidationError(f'Fotka je příliš velká (maximálně {PHOTO_MAX_BYTES // (1024 * 1024)} MB).')
    try:
        return shrink_photo(photo)
    except UnreadablePhoto:
        raise forms.ValidationError('Soubor se nepodařilo otevřít jako fotku.') from None


class MultipleFileInput(forms.FileInput):
    """A file input that reads every file posted under its name and renders `multiple`."""

    allow_multiple_selected = True


class CameraInput(MultipleFileInput):
    """Reads every shot posted under its name, but renders without `multiple`.

    `multiple` beside `capture` behaves unpredictably on phones. `photo_picker.js`
    posts several shots as several inputs.
    """

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['attrs'].pop('multiple', None)
        return context


class MultipleFileField(forms.FileField):
    """A list of uploads, each checked as `FileField` checks its one (validators included).

    Nothing posted is an empty list, so the views never tell None from [].
    """

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(item, initial) for item in data if item]
        return [single(data, initial)] if data else []


def _hours_field(min_value=Decimal('0.5'), max_value=WORKER_HOURS_MAX, **kwargs):
    """Hours as the job form takes them: half-hour steps, a person's day at most."""
    return forms.DecimalField(
        min_value=min_value, max_value=max_value, step_size=Decimal('0.5'), decimal_places=1, **kwargs
    )


def _offer_recorded(field, keep):
    """Add the retired catalog records in `keep` back to an active-only picker.

    Without this, `job_edit` renders a row naming a retired record blank, and
    saving it drops the row.
    """
    if not keep:
        return
    field.queryset = field.queryset.model.objects.filter(Q(is_active=True) | Q(pk__in=keep))


def collaborator_queryset(user, viewer=None):
    """Who may be named on a job authored by `user`.

    `user` is excluded. `viewer`, whoever fills the form in (default `user`),
    sets the width: a plain worker may only name other workers.
    """
    viewer = viewer if viewer is not None else user
    queryset = User.objects.all().order_by('username')
    if user is not None:
        queryset = queryset.exclude(pk=user.pk)
    if viewer is not None and not viewer.is_manager_or_admin:
        queryset = queryset.filter(role=User.Role.WORKER)
    return queryset


class EntryForm(forms.Form):
    """The fields a job and a refuelling share: optional notes and a date that isn't in the future."""

    notes = forms.CharField(
        required=False,
        max_length=NOTES_MAX_LENGTH,
        label='Poznámky',
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Doplňující poznámky (volitelné)'}),
    )
    # `format` is required: the cs locale would render 02.09.2026, which
    # <input type="date"> shows as blank.
    performed_on = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        label='Datum provedení',
    )

    def clean(self):
        cleaned_data = super().clean()
        performed_on = cleaned_data.get('performed_on')
        if performed_on and performed_on > timezone.localdate():
            self.add_error('performed_on', 'Datum provedení nemůže být v budoucnosti.')
        return cleaned_data


class WorkOrderForm(EntryForm):
    """The job's own fields."""

    # Active locations, plus `keep_location` on `job_edit` (see `_offer_recorded`).
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True),
        label='Lokace',
        empty_label='Vyberte lokaci',
    )

    description = forms.CharField(
        max_length=DESCRIPTION_MAX_LENGTH,
        label='Popis',
        widget=forms.TextInput(attrs={'placeholder': 'Popis provedené práce'}),
    )
    hours = _hours_field(label='Moje hodiny', widget=forms.NumberInput(attrs={'placeholder': 'Odpracované hodiny'}))
    # Gallery picks. Each file becomes a new `JobPhoto`; none replaces a stored one.
    # No `capture` here: it would hide the gallery.
    photo = MultipleFileField(
        required=False,
        label='Fotky',
        widget=MultipleFileInput(attrs={'accept': 'image/*'}),
        validators=[FileExtensionValidator(PHOTO_EXTENSIONS)],
    )
    # Camera shots. Chrome and Opera on Android offer no camera from `photo`, and
    # `capture` would hide the gallery, hence a second input. `clean()` merges it
    # into `photo`.
    camera_photo = MultipleFileField(
        required=False,
        label='Vyfotit',
        widget=CameraInput(attrs={'accept': 'image/*', 'capture': 'environment'}),
        validators=[FileExtensionValidator(PHOTO_EXTENSIONS)],
    )

    def __init__(self, *args, user=None, keep_location=None, photos=None, **kwargs):
        super().__init__(*args, **kwargs)
        _offer_recorded(self.fields['location'], keep_location)
        # `job_edit` passes the job's stored photos; each gets an „Odebrat" box.
        self.stored_photos = photos
        if photos is not None:
            self.fields['remove_photos'] = forms.ModelMultipleChoiceField(
                queryset=photos,
                required=False,
                label='Odebrat',
                widget=forms.CheckboxSelectMultiple,
            )
        # Only managers and admins may record a job for someone else.
        if user is None or not user.is_manager_or_admin:
            return
        self.fields['author'] = forms.ModelChoiceField(
            queryset=collaborator_queryset(user),
            required=False,
            label='Zapsat za',
            empty_label='Za sebe',
        )
        self.fields['hours'].label = 'Odpracované hodiny'

    def _clean_photos(self, cleaned_data):
        """Put every new photo from both inputs on `photo`, shrunk and ready to store.

        The count (including kept photos) and the total size span both inputs,
        and are checked before anything is decoded.
        """
        if 'photo' in self.errors or 'camera_photo' in self.errors:
            # The extension validator refused one; nothing is worth decoding.
            return
        uploads = cleaned_data.get('photo', []) + cleaned_data.get('camera_photo', [])
        cleaned_data['photo'] = []
        if not uploads:
            return
        kept = 0
        if self.stored_photos is not None:
            kept = self.stored_photos.count() - len(cleaned_data.get('remove_photos') or ())
        if kept + len(uploads) > PHOTO_MAX_COUNT:
            already = f', už uloženo {kept}' if kept else ''
            self.add_error(
                'photo', f'Zakázka může mít nejvýše {PHOTO_MAX_COUNT} fotek (vybráno {len(uploads)}{already}).'
            )
            return
        if sum(upload.size for upload in uploads) > PHOTO_MAX_TOTAL_BYTES:
            self.add_error(
                'photo',
                f'Fotky jsou dohromady příliš velké (najednou nejvýše {PHOTO_MAX_TOTAL_BYTES // (1024 * 1024)} MB).',
            )
            return
        shrunk, errors = [], []
        for upload in uploads:
            try:
                shrunk.append(clean_photo_upload(upload))
            except forms.ValidationError as exc:
                errors.extend(f'{upload.name}: {message}' for message in exc.messages)
        if errors:
            self.add_error('photo', errors)
        else:
            cleaned_data['photo'] = shrunk

    def author_or(self, submitter):
        """Whose job this is: the person picked in „Zapsat za", else the submitter."""
        return self.cleaned_data.get('author') or submitter

    def clean(self):
        cleaned_data = super().clean()
        self._clean_photos(cleaned_data)
        return cleaned_data


class RefuellingForm(EntryForm):
    """A refuelling's own fields; its machines are the `refuels` section."""


class UniqueChoiceFormSet(forms.BaseFormSet):
    """A job section whose rows must each name a different thing.

    `clean()` refuses a repeat. Unbound formsets also hide choices other rows
    already took; bound ones don't, or a duplicate would fail as
    „Vyberte platnou možnost." instead of with `duplicate_error`.
    """

    unique_field = None
    duplicate_error = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self._hide_taken_choices()

    def _row_choices(self):
        """Each row's choice as a pk string, or None.

        Initial values arrive as ints from `job_edit` and as strings from a raw POST.
        """
        choices = []
        for form in self.forms:
            value = form.initial.get(self.unique_field)
            value = '' if value is None else str(value)
            choices.append(value if value.isdigit() else None)
        return choices

    def _hide_taken_choices(self):
        chosen = self._row_choices()
        taken = {value for value in chosen if value}
        if not taken:
            return
        for form, own in zip(self.forms, chosen, strict=True):
            # A row keeps its own choice, or it would render blank.
            drop = taken - {own}
            if drop:
                field = form.fields[self.unique_field]
                field.queryset = field.queryset.exclude(pk__in=drop)

    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            value = form.cleaned_data.get(self.unique_field) if hasattr(form, 'cleaned_data') else None
            if value is None:
                continue
            if value in seen:
                form.add_error(None, self.duplicate_error.format(name=value))
            seen.add(value)


class MaterialRowFormSet(UniqueChoiceFormSet):
    unique_field = 'material'
    duplicate_error = 'Materiál „{name}“ je v této sekci vybraný víckrát. Sečtěte množství do jednoho řádku.'


class MachineRowFormSet(UniqueChoiceFormSet):
    unique_field = 'machine'
    duplicate_error = 'Stroj „{name}“ je vybraný víckrát. Zapište ho jen do jednoho řádku.'


class RefuelRowFormSet(UniqueChoiceFormSet):
    unique_field = 'machine'
    duplicate_error = (
        'Stroj „{name}“ je vybraný víckrát. Sečtěte litry do jednoho řádku, '
        'nebo tankování z jiného zdroje zapište zvlášť.'
    )


class WorkerRowFormSet(UniqueChoiceFormSet):
    unique_field = 'user'
    duplicate_error = 'Pracovník „{name}“ je vybraný víckrát. Sečtěte hodiny do jednoho řádku.'


class RowForm(forms.Form):
    """One row of a job section: blank, or filled in completely.

    `catalog_field` names the picker `keep` widens (see `_offer_recorded`).
    """

    incomplete_error = None
    catalog_field = None

    def __init__(self, *args, keep=None, **kwargs):
        super().__init__(*args, **kwargs)
        if self.catalog_field:
            _offer_recorded(self.fields[self.catalog_field], keep)

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            # A failed field is missing from cleaned_data and would look
            # half-filled, burying the real error under `incomplete_error`.
            return cleaned_data
        self.check_row(cleaned_data)
        return cleaned_data

    def check_row(self, cleaned_data):
        """The row's own rule: blank, or filled in completely. `MachineUsageForm` overrides it."""
        filled = [cleaned_data.get(name) for name in self.fields]
        if any(filled) and not all(filled):
            raise forms.ValidationError(self.incomplete_error)


class MovementItemForm(RowForm):
    catalog_field = 'material'
    incomplete_error = 'Vyplňte materiál i množství, nebo řádek nechte prázdný.'

    material = forms.ModelChoiceField(
        queryset=Material.objects.filter(is_active=True),
        required=False,
        label='Materiál',
        empty_label='Materiál',
    )
    quantity = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_value=TONS_MAX,
        decimal_places=2,
        required=False,
        label='Množství (t)',
        # Row boxes are narrow, so the placeholder is a bare unit and `title`
        # carries the full name (also the accessible name; rows have no <label>).
        widget=forms.NumberInput(attrs={'placeholder': 't', 'title': 'Množství (t)', 'class': 'tons'}),
    )


class MachineUsageForm(RowForm):
    """One machine on a job: its meter reading and the tonnage it processed.

    Only the machine is required; a typed 0 counts as blank, and a machine alone
    is a usage row with unknown numbers. Fuel is not recorded here (`RefuelRowForm`).
    """

    catalog_field = 'machine'
    missing_machine_error = 'Vyberte stroj, nebo řádek nechte prázdný.'

    machine = forms.ModelChoiceField(
        queryset=Machine.objects.filter(is_active=True),
        required=False,
        label='Stroj',
        empty_label='Stroj',
    )
    # The hour-meter reading, not time run; Stroje derives run time from readings.
    hours = _hours_field(
        min_value=Decimal('0'),
        max_value=METER_READING_MAX,
        required=False,
        label='Stav motohodin',
        widget=forms.NumberInput(attrs={'placeholder': 'mth', 'title': 'Stav motohodin', 'class': 'mth'}),
    )
    # Not part of the mass balance: chained machines each process the same material.
    tons = forms.DecimalField(
        min_value=Decimal('0'),
        max_value=TONS_MAX,
        decimal_places=2,
        required=False,
        label='Tuny',
        widget=forms.NumberInput(attrs={'placeholder': 't', 'title': 'Tuny', 'class': 'tons'}),
    )

    def _zero_is_blank(self, name):
        """A typed 0 means "not recorded": stored as unknown, never as a zero."""
        value = self.cleaned_data.get(name)
        return None if value == 0 else value

    def clean_hours(self):
        return self._zero_is_blank('hours')

    def clean_tons(self):
        return self._zero_is_blank('tons')

    def check_row(self, cleaned_data):
        machine = cleaned_data.get('machine')
        numbers = (cleaned_data.get('hours'), cleaned_data.get('tons'))
        if machine is None and any(value is not None for value in numbers):
            raise forms.ValidationError(self.missing_machine_error)


class RefuelRowForm(RowForm):
    """One machine on a refuelling: how much fuel, and optionally where it came from.

    Blank, or a machine and litres; „Zdroj" may be left empty.
    """

    catalog_field = 'machine'
    incomplete_error = 'Vyplňte stroj i litry, nebo řádek nechte prázdný.'

    machine = forms.ModelChoiceField(
        queryset=Machine.objects.filter(is_active=True),
        required=False,
        label='Stroj',
        empty_label='Stroj',
    )
    # Active sources, plus `keep_source` on `refuel_edit` (see `_offer_recorded`).
    source = forms.ModelChoiceField(
        queryset=FuelSource.objects.filter(is_active=True),
        required=False,
        label='Zdroj',
        empty_label='Zdroj',
    )
    litres = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_value=LITRES_MAX,
        decimal_places=2,
        required=False,
        label='Natankováno (l)',
        # Three characters wide, unlike the tonnes boxes: two pickers share this row.
        widget=forms.NumberInput(attrs={'placeholder': 'l', 'title': 'Natankováno (l)'}),
    )

    def __init__(self, *args, keep_source=None, **kwargs):
        super().__init__(*args, **kwargs)
        _offer_recorded(self.fields['source'], keep_source)

    def check_row(self, cleaned_data):
        # A source alone is half a row too.
        filled = [cleaned_data.get(name) for name in self.fields]
        if any(filled) and not (cleaned_data.get('machine') and cleaned_data.get('litres')):
            raise forms.ValidationError(self.incomplete_error)


class WorkerHoursForm(RowForm):
    """A collaborator and their hours. Naming someone here is what makes them a collaborator."""

    incomplete_error = 'Vyplňte pracovníka a počet hodin, nebo řádek nechte prázdný.'

    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label='Pracovník',
        empty_label='Pracovník',
    )
    hours = _hours_field(
        required=False,
        label='Hodiny',
        widget=forms.NumberInput(attrs={'placeholder': 'h', 'title': 'Hodiny', 'class': 'hours'}),
    )

    def __init__(self, *args, user=None, viewer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = collaborator_queryset(user, viewer)


@dataclass(frozen=True)
class JobSection:
    """One row section of an entry form. `prefix` also names its row buttons (`add_<prefix>`).

    `sum_from` names another section whose `sum_field` total this section's
    single row can be filled with (the `sum_<prefix>` button): a screening job
    weighs only what comes out.

    `mirror_from` names another section whose `mirror_source` total is copied
    into this section's `mirror_field` on every row that names its catalog
    record: the machines usually process exactly what the job consumed.
    """

    prefix: str
    context_name: str
    title: str
    row_form: type
    formset_class: type
    sum_from: str = ''
    sum_field: str = ''
    mirror_from: str = ''
    mirror_source: str = ''
    mirror_field: str = ''


# In render order. A new section only needs adding here.
JOB_SECTIONS = (
    JobSection('workers', 'worker_formset', 'Spolupracovníci', WorkerHoursForm, WorkerRowFormSet),
    JobSection(
        'consumed',
        'consumed_formset',
        'Spotřebováno',
        MovementItemForm,
        MaterialRowFormSet,
        sum_from='produced',
        sum_field='quantity',
    ),
    JobSection('produced', 'produced_formset', 'Vyrobeno', MovementItemForm, MaterialRowFormSet),
    JobSection(
        'machines',
        'machine_formset',
        'Použité stroje',
        MachineUsageForm,
        MachineRowFormSet,
        mirror_from='consumed',
        mirror_source='quantity',
        mirror_field='tons',
    ),
)

# The refuelling form's one section.
REFUEL_SECTIONS = (JobSection('refuels', 'refuel_formset', 'Tankované stroje', RefuelRowForm, RefuelRowFormSet),)


def job_row_formset(section, *, data=None, rows=(), blank_rows=1, form_kwargs=None):
    """One section's formset: bound to `data`, or `rows` plus `blank_rows` empty ones.

    `extra` is a class attribute, hence a new formset class per call.
    """
    formset_class = forms.formset_factory(section.row_form, formset=section.formset_class, extra=blank_rows)
    return formset_class(data, prefix=section.prefix, initial=list(rows), form_kwargs=form_kwargs or {})


class DateRangeFilterForm(forms.Form):
    """The date range shared by every filter form.

    Subclasses set `lookups`, `date_lookup`, and `field_order` so the dates render last.
    """

    date_lookup = 'performed_on'
    # Field name -> ORM lookup, or a callable returning a Q.
    lookups = {}

    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum od')
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum do')

    def filter(self, queryset):
        """`queryset` narrowed by this form. Unbound shows everything; invalid shows nothing."""
        if not self.is_bound:
            return queryset
        if not self.is_valid():
            return queryset.none()
        data = self.cleaned_data
        for name, lookup in self.lookups.items():
            if data.get(name):
                queryset = queryset.filter(lookup(data[name]) if callable(lookup) else Q(**{lookup: data[name]}))
        if data.get('date_from'):
            queryset = queryset.filter(**{f'{self.date_lookup}__gte': data['date_from']})
        if data.get('date_to'):
            queryset = queryset.filter(**{f'{self.date_lookup}__lte': data['date_to']})
        return queryset

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get('date_from')
        date_to = cleaned_data.get('date_to')
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError('„Datum od“ musí být dřívější nebo stejné jako „datum do“.')
        return cleaned_data


def participation_filter(user):
    """A user "worked on" a job if they submitted it or were named a collaborator."""
    return Q(created_by=user) | Q(collaborators=user)


class TimeWorkedFilterForm(DateRangeFilterForm):
    field_order = ['worker', 'date_from', 'date_to']
    lookups = {'worker': participation_filter}

    worker = forms.ModelChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=False,
        label='Pracovník',
        empty_label='Všichni pracovníci',
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and not user.is_manager_or_admin:
            # Cosmetic; the view's queryset is what scopes a worker.
            del self.fields['worker']


class MachineFilterForm(DateRangeFilterForm):
    """Filters Stroje's totals and usage rows (and, as `RefuelFilterForm`, its fuel). Manager-only."""

    field_order = ['machine', 'created_by', 'date_from', 'date_to']
    date_lookup = 'work_order__performed_on'
    lookups = {'machine': 'machine', 'created_by': 'work_order__created_by'}

    machine = forms.ModelChoiceField(
        queryset=Machine.objects.all().order_by('name'),
        required=False,
        label='Stroj',
        empty_label='Všechny stroje',
    )
    created_by = forms.ModelChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=False,
        label='Vytvořil',
        empty_label='Všichni uživatelé',
    )


class RefuelFilterForm(MachineFilterForm):
    """Stroje's filter applied to fill-ups, whose date and author are on the refuelling."""

    date_lookup = 'refuelling__performed_on'
    lookups = {'machine': 'machine', 'created_by': 'refuelling__created_by'}


class MaterialFilterForm(DateRangeFilterForm):
    """Filters Materiál's totals and line items.

    Offers retired materials too, the only way to reach their rows.
    `movement_type` narrows only „Detail položek" (`detail_rows`), so it is not
    in `lookups`: the summary already splits the two directions into columns.
    """

    field_order = ['material', 'movement_type', 'location', 'date_from', 'date_to']
    date_lookup = 'work_order__performed_on'
    lookups = {'material': 'material', 'location': 'work_order__location'}

    material = forms.ModelChoiceField(
        queryset=Material.objects.all().order_by('name'),
        required=False,
        label='Materiál',
        empty_label='Všechny materiály',
    )
    # Retired locations too, like `material`: the only way to reach their rows.
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by('name'),
        required=False,
        label='Lokace',
        empty_label='Všechny lokace',
    )
    movement_type = forms.ChoiceField(
        choices=[('', 'Všechny druhy')] + StockMovement.MovementType.choices,
        required=False,
        label='Druh pohybu',
    )

    def detail_rows(self, movements):
        """`movements` (already `filter`ed) narrowed to the chosen direction."""
        if self.is_bound and self.is_valid() and self.cleaned_data['movement_type']:
            return movements.filter(movement_type=self.cleaned_data['movement_type'])
        return movements


class JobFilterForm(DateRangeFilterForm):
    """Filters for Přehled (manager-only)."""

    field_order = ['created_by', 'location', 'status', 'date_from', 'date_to']
    lookups = {'created_by': 'created_by', 'location': 'location', 'status': 'status'}

    created_by = forms.ModelChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=False,
        label='Vytvořil',
        empty_label='Všichni uživatelé',
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by('name'),
        required=False,
        label='Lokace',
        empty_label='Všechny lokace',
    )
    status = forms.ChoiceField(
        choices=[('', 'Všechny stavy')] + ReviewStatus.choices,
        required=False,
        label='Stav',
    )


class RefuellingFilterForm(DateRangeFilterForm):
    """Filters the refuelling list (manager-only)."""

    field_order = ['created_by', 'status', 'date_from', 'date_to']
    lookups = {'created_by': 'created_by', 'status': 'status'}

    created_by = forms.ModelChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=False,
        label='Vytvořil',
        empty_label='Všichni uživatelé',
    )
    status = forms.ChoiceField(
        choices=[('', 'Všechny stavy')] + ReviewStatus.choices,
        required=False,
        label='Stav',
    )
