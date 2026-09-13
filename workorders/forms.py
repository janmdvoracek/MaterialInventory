from dataclasses import dataclass
from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from machines.models import Machine
from materials.models import Material

from .models import WorkOrder

# The hours columns are decimal(12, 2), so 10 integer digits. DecimalValidator
# allows max_digits - decimal_places integer digits: 10 + the 1 decimal place
# typed here = 11. Wider, and an oversized value is a 500 instead of a field error.
HOURS_MAX_DIGITS = 11


def _hours_field(**kwargs):
    """Hours as the job form takes them: half-hour steps, capped at the column width."""
    return forms.DecimalField(
        min_value=Decimal('0.5'), max_digits=HOURS_MAX_DIGITS, step_size=Decimal('0.5'), decimal_places=1, **kwargs
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


class WorkOrderForm(forms.Form):
    description = forms.CharField(
        required=False,
        max_length=255,
        label='Popis',
        widget=forms.TextInput(attrs={'placeholder': 'Popis provedené práce (volitelné)'}),
    )
    hours = _hours_field(label='Moje hodiny', widget=forms.NumberInput(attrs={'placeholder': 'Odpracované hodiny'}))
    # `format` is required: the cs locale would render 02.09.2026, which
    # <input type="date"> shows as blank.
    performed_on = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        label='Datum provedení',
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
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

    def author_or(self, submitter):
        """Whose job this is: the person picked in „Zapsat za", else the submitter."""
        return self.cleaned_data.get('author') or submitter

    def clean(self):
        cleaned_data = super().clean()
        performed_on = cleaned_data.get('performed_on')
        if performed_on and performed_on > timezone.localdate():
            self.add_error('performed_on', 'Datum provedení nemůže být v budoucnosti.')
        return cleaned_data


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
        for form, own in zip(self.forms, chosen):
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
    duplicate_error = 'Stroj „{name}“ je vybraný víckrát. Sečtěte motohodiny a tuny do jednoho řádku.'


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
        filled = [cleaned_data.get(name) for name in self.fields]
        if any(filled) and not all(filled):
            raise forms.ValidationError(self.incomplete_error)
        return cleaned_data


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
        max_digits=7,
        decimal_places=2,
        required=False,
        label='Množství (t)',
        widget=forms.NumberInput(attrs={'placeholder': 'Množství (t)'}),
    )


class MachineUsageForm(RowForm):
    catalog_field = 'machine'
    incomplete_error = 'Vyplňte stroj, motohodiny a tuny, nebo řádek nechte prázdný.'

    machine = forms.ModelChoiceField(
        queryset=Machine.objects.filter(is_active=True),
        required=False,
        label='Stroj',
        empty_label='Stroj',
    )
    hours = _hours_field(required=False, label='Hodiny', widget=forms.NumberInput(attrs={'placeholder': 'Motohodiny'}))
    # Not part of the mass balance: chained machines each process the same material.
    tons = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=7,
        decimal_places=2,
        required=False,
        label='Tuny',
        widget=forms.NumberInput(attrs={'placeholder': 'Tuny'}),
    )


class WorkerHoursForm(RowForm):
    """A collaborator and their hours. Naming someone here is what makes them a collaborator."""

    incomplete_error = 'Vyplňte pracovníka a počet hodin, nebo řádek nechte prázdný.'

    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label='Pracovník',
        empty_label='Pracovník',
    )
    hours = _hours_field(required=False, label='Hodiny', widget=forms.NumberInput(attrs={'placeholder': 'Hodiny'}))

    def __init__(self, *args, user=None, viewer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = collaborator_queryset(user, viewer)


@dataclass(frozen=True)
class JobSection:
    """One row section of the job form. `prefix` also names its row buttons (`add_<prefix>`)."""

    prefix: str
    context_name: str
    title: str
    row_form: type
    formset_class: type


# In render order. A new section only needs adding here.
JOB_SECTIONS = (
    JobSection('workers', 'worker_formset', 'Spolupracovníci', WorkerHoursForm, WorkerRowFormSet),
    JobSection('consumed', 'consumed_formset', 'Spotřebováno', MovementItemForm, MaterialRowFormSet),
    JobSection('produced', 'produced_formset', 'Vyrobeno', MovementItemForm, MaterialRowFormSet),
    JobSection('machines', 'machine_formset', 'Použité stroje', MachineUsageForm, MachineRowFormSet),
)


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
    """Filters Stroje's totals and usage rows. Manager-only, so nothing is hidden."""

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


class MaterialFilterForm(DateRangeFilterForm):
    """Filters Materiál's totals and line items.

    Offers retired materials too, the only way to reach their rows.
    """

    field_order = ['material', 'date_from', 'date_to']
    date_lookup = 'work_order__performed_on'
    lookups = {'material': 'material'}

    material = forms.ModelChoiceField(
        queryset=Material.objects.all().order_by('name'),
        required=False,
        label='Materiál',
        empty_label='Všechny materiály',
    )


class JobFilterForm(DateRangeFilterForm):
    """Filters for Přehled (manager-only)."""

    field_order = ['created_by', 'status', 'date_from', 'date_to']
    lookups = {'created_by': 'created_by', 'status': 'status'}

    created_by = forms.ModelChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=False,
        label='Vytvořil',
        empty_label='Všichni uživatelé',
    )
    status = forms.ChoiceField(
        choices=[('', 'Všechny stavy')] + WorkOrder.Status.choices,
        required=False,
        label='Stav',
    )
