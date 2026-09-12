from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from materials.models import Machine, Material

from .models import WorkOrder

# The hours fields below are typed with one decimal place but stored in columns
# declared `max_digits=12, decimal_places=2` (`WorkerHours.hours`,
# `MachineUsage.hours`). Postgres spends the scale whether the value uses it or
# not, so those columns hold at most 12 - 2 = 10 *integer* digits — and a form
# wider than its column is not a harmless mismatch here: the value passes
# validation, reaches the INSERT and comes back as `DataError: numeric field
# overflow`, i.e. an unhandled 500 rather than a message on the field.
#
# So the form is capped at the column's integer width, expressed the way
# `DecimalValidator` reads it: that validator allows `max_digits -
# decimal_places` integer digits, so the ten the column holds plus the one
# decimal place typed here is eleven. Off by one in either direction is a real
# bug — 12 is the overflow above, 10 would refuse a value that stores fine.
#
# `quantity` and `tons` need no such cap: at `max_digits=7` they are already far
# narrower than the columns they land in.
HOURS_MAX_DIGITS = 11


def _offer_recorded(field, keep):
    """Let a catalog picker keep offering the records a job already names.

    The row pickers are scoped to `is_active=True`, so a retired material or
    machine cannot land on a *new* job. A job recorded before the retirement
    still names one, though, and a `ModelChoiceField` whose queryset excludes
    the stored pk renders that row with nothing selected and then rejects the pk
    on submit. Saving the form as rendered drops the row silently: the machine
    usage is deleted outright by `_write_job_rows`, and a vanished consumed row
    leaves the job stuck behind a mass-balance error no edit can clear.

    So `job_edit` passes the pks the job already uses, and those are added back
    — those and nothing else, which is what keeps retirement meaningful
    everywhere except the one job that predates it. Assumes the field's base
    queryset is the active catalog, which is how both callers declare it.
    """
    if not keep:
        return
    field.queryset = field.queryset.model.objects.filter(Q(is_active=True) | Q(pk__in=keep))


def collaborator_queryset(user, viewer=None):
    """People who may be named as having worked a job whose author is `user`.

    `viewer` is whoever is filling the form in: the submitter, or the manager
    recording on somebody's behalf or correcting their job. It decides how wide
    the list is, while `user` is only ever excluded from it. The two differ only
    when a manager acts for someone else — the role restriction below is there
    to stop a *worker* putting hours on a manager, not to stop a manager
    recording that a manager worked the job.
    """
    viewer = viewer if viewer is not None else user
    queryset = User.objects.all().order_by('username')
    if user is not None:
        # Can't collaborate with yourself — you're already the creator.
        queryset = queryset.exclude(pk=user.pk)
    if viewer is not None and not viewer.is_manager_or_admin:
        # Plain workers only collaborate with other workers, not
        # managers/admins.
        queryset = queryset.filter(role=User.Role.WORKER)
    return queryset


class WorkOrderForm(forms.Form):
    description = forms.CharField(
        required=False,
        max_length=255,
        label='Popis',
        widget=forms.TextInput(attrs={'placeholder': 'Popis provedené práce (volitelné)'}),
    )
    hours = forms.DecimalField(
        min_value=Decimal('0.5'),
        max_digits=HOURS_MAX_DIGITS,
        step_size=Decimal('0.5'),
        decimal_places=1,
        label='Moje hodiny',
        widget=forms.NumberInput(attrs={'placeholder': 'Odpracované hodiny'}),
    )
    # Pre-filled with today, so the common case is already answered and
    # back-dating is just editing the box. **`format` is not optional here**: an
    # unbound field with a python `date` initial renders through the `cs`
    # DATE_INPUT_FORMATS, i.e. `02.09.2026`, which `<input type="date">` rejects
    # outright and shows as blank. ISO is what the widget reads and writes.
    performed_on = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        label='Datum provedení',
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is None or not user.is_manager_or_admin:
            # Plain workers record their own work and nobody else's. The field
            # simply is not on their form, so an `author` in the POST is not
            # something the view has to defend against — it is never cleaned.
            return
        self.fields['author'] = forms.ModelChoiceField(
            queryset=collaborator_queryset(user),
            required=False,
            label='Zapsat za',
            empty_label='Za sebe',
        )
        # With somebody else possibly on the receiving end, „Moje hodiny" would
        # be a lie half the time.
        self.fields['hours'].label = 'Odpracované hodiny'

    def author_or(self, submitter):
        """Whose job this is: the person picked in „Zapsat za", else the submitter."""
        return self.cleaned_data.get('author') or submitter

    def clean(self):
        cleaned_data = super().clean()
        performed_on = cleaned_data.get('performed_on')
        if performed_on and performed_on > timezone.localdate():
            # Recording work that has not happened yet is a typo, not a plan.
            self.add_error('performed_on', 'Datum provedení nemůže být v budoucnosti.')
        return cleaned_data


class UniqueChoiceFormSet(forms.BaseFormSet):
    """A job section whose rows must each name a different thing.

    One material, machine or person belongs on one row of a section: two rows
    naming the same one are two halves of a number that should have been typed
    once, and nothing downstream can tell them apart afterwards. The rule is
    per section, so the same material may still be consumed *and* produced by
    one job — those are two different statements about it.

    Two halves, and both are needed:

    - `clean()` refuses a duplicate and says which one, on the offending row.
      This is the rule; it is enforced on the POST and cannot be got around.
    - `_hide_taken_choices()` drops what other rows already took out of a row's
      dropdown, so on an unbound page the duplicate is not offered in the first
      place. Presentation only, and it can only be as fresh as the last render:
      there is no JavaScript in this app, so the list a row is showing was built
      when the page was, and the page is rebuilt on „+ další řádek" (which is
      the tap that asks for an empty row to fill) and on every re-render after
      an error. Picking the same thing twice between two renders is exactly what
      `clean()` is there for.

    Only unbound formsets get the hiding. A bound one is being validated, and
    narrowing a field's queryset there would turn a duplicate into
    „Vyberte platnou možnost." on whichever row lost the race, instead of the
    message below.
    """

    # The select the rule applies to, and the Czech complaint about a repeat.
    unique_field = None
    duplicate_error = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self._hide_taken_choices()

    def _row_choices(self):
        """What each row currently names, as a pk string, `None` for an empty row.

        Read off `initial` because this runs on unbound formsets only. The
        values arrive as ints from `job_edit` (a stored `material_id`) and as
        strings from a „+ další řádek" rebuild (raw POST), so they are
        normalised to strings and anything that is not a pk is ignored.
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
            # Every row keeps its own choice — it is the one that has to
            # re-select an option when the row renders.
            drop = taken - {own}
            if drop:
                field = form.fields[self.unique_field]
                field.queryset = field.queryset.exclude(pk__in=drop)

    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            # A row that failed its own validation has nothing to compare and is
            # already carrying an error of its own.
            value = form.cleaned_data.get(self.unique_field) if hasattr(form, 'cleaned_data') else None
            if value is None:
                continue
            if value in seen:
                # On the row, not as a non-form error: the message names one row
                # of four and belongs where the reader can see which.
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


class MovementItemForm(forms.Form):
    # required=False because a whole row may be left blank, but these are still
    # entry fields — the blank option is a prompt, not an "all" filter.
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
        # The app records tonnes and nothing else, so the unit is part of
        # the prompt rather than a column on the material.
        label='Množství (t)',
        widget=forms.NumberInput(attrs={'placeholder': 'Množství (t)'}),
    )

    def __init__(self, *args, keep=None, **kwargs):
        super().__init__(*args, **kwargs)
        _offer_recorded(self.fields['material'], keep)

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            # A field that failed its own validation is absent from
            # cleaned_data, which reads here exactly like a half-filled row —
            # so the check below would add "fill in both" on top of the real
            # complaint, and that is the one the reader would act on. It is
            # also the wrong advice: they *did* fill both in.
            return cleaned_data
        filled = [cleaned_data.get('material'), cleaned_data.get('quantity')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Vyplňte materiál i množství, nebo řádek nechte prázdný.')
        return cleaned_data


ConsumedFormSet = forms.formset_factory(MovementItemForm, formset=MaterialRowFormSet, extra=1)
ProducedFormSet = forms.formset_factory(MovementItemForm, formset=MaterialRowFormSet, extra=1)


class MachineUsageForm(forms.Form):
    machine = forms.ModelChoiceField(
        queryset=Machine.objects.filter(is_active=True),
        required=False,
        label='Stroj',
        empty_label='Stroj',
    )
    hours = forms.DecimalField(
        min_value=Decimal('0.5'),
        max_digits=HOURS_MAX_DIGITS,
        step_size=Decimal('0.5'),
        decimal_places=1,
        required=False,
        label='Hodiny',
        widget=forms.NumberInput(attrs={'placeholder': 'Motohodiny'}),
    )
    # Tonnage this machine put through on this job. Unrelated to the job's
    # own consumed/produced totals — several chained machines each process the
    # same material, so these do not add up to the mass balance and are not
    # checked against it.
    tons = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=7,
        decimal_places=2,
        required=False,
        label='Tuny',
        widget=forms.NumberInput(attrs={'placeholder': 'Tuny'}),
    )

    def __init__(self, *args, keep=None, **kwargs):
        super().__init__(*args, **kwargs)
        _offer_recorded(self.fields['machine'], keep)

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            # See MovementItemForm.clean: a field error already removed the
            # value from cleaned_data, so the emptiness check below would bury
            # the real message under a wrong one.
            return cleaned_data
        filled = [cleaned_data.get('machine'), cleaned_data.get('hours'), cleaned_data.get('tons')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Vyplňte stroj, motohodiny a tuny, nebo řádek nechte prázdný.')
        return cleaned_data


MachineUsageFormSet = forms.formset_factory(MachineUsageForm, formset=MachineRowFormSet, extra=1)


class WorkerHoursForm(forms.Form):
    """One collaborator and the hours they worked. Naming someone here is what
    makes them a collaborator on the job — there is no separate picker."""

    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label='Pracovník',
        empty_label='Pracovník',
    )
    # The row layout has no room for a visible label, so the placeholder is it —
    # same reason the selects use the bare noun as their `empty_label`.
    hours = forms.DecimalField(
        min_value=Decimal('0.5'),
        max_digits=HOURS_MAX_DIGITS,
        step_size=Decimal('0.5'),
        decimal_places=1,
        required=False,
        label='Hodiny',
        widget=forms.NumberInput(attrs={'placeholder': 'Hodiny'}),
    )

    def __init__(self, *args, user=None, viewer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = collaborator_queryset(user, viewer)

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            # See MovementItemForm.clean.
            return cleaned_data
        filled = [cleaned_data.get('user'), cleaned_data.get('hours')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Vyplňte pracovníka a počet hodin, nebo řádek nechte prázdný.')
        return cleaned_data


WorkerHoursFormSet = forms.formset_factory(WorkerHoursForm, formset=WorkerRowFormSet, extra=1)


# The four row sections of the job form, in the order `_job_form_fields.html`
# renders them: the formset prefix — which is also the suffix of the
# „+ další řádek" button that grows that section, `add_workers` and friends —
# the form a single row is made of, and the formset class that holds the
# section's no-duplicates rule. `_resized_job_forms` in views.py walks this, so
# a fifth section needs adding here and nowhere else.
JOB_SECTIONS = (
    ('workers', WorkerHoursForm, WorkerRowFormSet),
    ('consumed', MovementItemForm, MaterialRowFormSet),
    ('produced', MovementItemForm, MaterialRowFormSet),
    ('machines', MachineUsageForm, MachineRowFormSet),
)


def job_row_formset(row_form, base_formset, *, prefix, rows, blank_rows, form_kwargs=None):
    """One section of the job form holding exactly `rows`, plus `blank_rows` empty ones.

    The formset classes above pad a fixed number of blanks onto whatever they
    are handed, which is what a fresh form wants and the opposite of what a page
    being re-rendered after „+ další řádek" wants — there the row count *is* the
    answer, and padding it again would add three rows per tap instead of one.
    `extra` is a class attribute, so the size has to be baked into a class
    rather than passed to the instance; `formset_factory` is a `type()` call and
    cheap enough to run per request.

    Handing `formset_factory` the section's own `base_formset` is what keeps
    the rebuilt page's dropdowns free of what the other rows already took — a
    plain `BaseFormSet` here would leave the new row offering the material one
    tap away from being a duplicate.

    The rows go in as `initial`, not `data`: the rebuilt page is unbound on
    purpose. See `_resized_job_forms`.
    """
    formset_class = forms.formset_factory(row_form, formset=base_formset, extra=blank_rows)
    return formset_class(prefix=prefix, initial=rows, form_kwargs=form_kwargs or {})


class DateRangeFilterForm(forms.Form):
    """The date range every filtered page has, and the one rule about it.

    Hodiny, Stroje and Přehled each scope by something of their own, but all
    three also narrow to a span of days, so the pair of fields and the
    „od ≤ do“ check live here instead of three times over. This is also what
    parses the dates the quick-range links write into the querystring
    (`_date_preset_links` in `workorders/views.py`).

    Subclasses declare their own fields and set `field_order`: `{{ form.as_p }}`
    renders in declaration order and fields inherited from a base class come
    first, which would otherwise put the dates above the picker they qualify.
    """

    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum od')
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum do')

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get('date_from')
        date_to = cleaned_data.get('date_to')
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError('„Datum od“ musí být dřívější nebo stejné jako „datum do“.')
        return cleaned_data


class TimeWorkedFilterForm(DateRangeFilterForm):
    field_order = ['worker', 'date_from', 'date_to']

    worker = forms.ModelChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=False,
        label='Pracovník',
        empty_label='Všichni pracovníci',
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and not user.is_manager_or_admin:
            # Workers only ever see their own hours, so picking someone else
            # would be a dead end.
            del self.fields['worker']


class MachineFilterForm(DateRangeFilterForm):
    """Filters the whole Stroje page — both the per-machine totals and the usage
    rows underneath them, which are two views of the same set of rows.

    Takes no `user`, unlike `TimeWorkedFilterForm`: the page is manager/admin
    only, so `created_by` is never a dead end and there is nobody to hide it
    from.
    """

    field_order = ['machine', 'created_by', 'date_from', 'date_to']

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
    """Filters the whole Materiál page — the per-material totals and the line
    items underneath them, which are two views of the same set of rows.

    Laid out like `MachineFilterForm` and manager/admin only for the same
    reason, but without its `created_by`: who typed a job in is a review
    question, not a material one.

    The queryset is every material, not just the active ones. A retired
    material still has history worth reading back, and the summary above is
    what limits itself to `is_active=True` — narrowing to one retired material
    here is the only way to see its rows at all.
    """

    field_order = ['material', 'date_from', 'date_to']

    material = forms.ModelChoiceField(
        queryset=Material.objects.all().order_by('name'),
        required=False,
        label='Materiál',
        empty_label='Všechny materiály',
    )


class JobFilterForm(DateRangeFilterForm):
    """Filters for the manager dashboard. No worker variant: the whole view is
    manager/admin only, so nothing has to be hidden from anyone."""

    field_order = ['created_by', 'status', 'date_from', 'date_to']

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
