from decimal import Decimal

from django import forms
from django.utils import timezone

from accounts.models import User
from materials.models import Machine, Material

from .models import WorkOrder


def collaborator_queryset(user):
    """People `user` may name as having worked a job alongside them."""
    queryset = User.objects.all().order_by('username')
    if user is not None:
        # Can't collaborate with yourself — you're already the creator.
        queryset = queryset.exclude(pk=user.pk)
        if not user.is_manager_or_admin:
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
        max_digits=12,
        step_size=Decimal('0.5'),
        decimal_places=1,
        label='Moje hodiny',
        widget=forms.NumberInput(attrs={'placeholder': 'Odpracované hodiny'}),
    )
    # The date input has no way to hide itself without JS, so the checkbox is
    # what decides whether it counts. Unticked, whatever is in the field is
    # ignored and `clean` fills in today — that way a stale value left in the
    # box can never silently back-date a job.
    use_custom_date = forms.BooleanField(required=False, label='Jiné datum než dnes')
    performed_on = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
        label='Datum provedení',
    )

    def clean(self):
        cleaned_data = super().clean()
        today = timezone.localdate()
        if not cleaned_data.get('use_custom_date'):
            cleaned_data['performed_on'] = today
            return cleaned_data
        performed_on = cleaned_data.get('performed_on')
        if not performed_on:
            self.add_error('performed_on', 'Zadejte datum provedení, nebo odškrtněte „Jiné datum než dnes“.')
        elif performed_on > today:
            # Recording work that has not happened yet is a typo, not a plan.
            self.add_error('performed_on', 'Datum provedení nemůže být v budoucnosti.')
        return cleaned_data


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

    def clean(self):
        cleaned_data = super().clean()
        filled = [cleaned_data.get('material'), cleaned_data.get('quantity')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Vyplňte materiál i množství, nebo řádek nechte prázdný.')
        return cleaned_data


ConsumedFormSet = forms.formset_factory(MovementItemForm, extra=3)
ProducedFormSet = forms.formset_factory(MovementItemForm, extra=3)


class MachineUsageForm(forms.Form):
    machine = forms.ModelChoiceField(
        queryset=Machine.objects.filter(is_active=True),
        required=False,
        label='Stroj',
        empty_label='Stroj',
    )
    hours = forms.DecimalField(
        min_value=Decimal('0.5'),
        max_digits=12,
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

    def clean(self):
        cleaned_data = super().clean()
        filled = [cleaned_data.get('machine'), cleaned_data.get('hours'), cleaned_data.get('tons')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Vyplňte stroj, motohodiny a tuny, nebo řádek nechte prázdný.')
        return cleaned_data


MachineUsageFormSet = forms.formset_factory(MachineUsageForm, extra=4)


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
        max_digits=12,
        step_size=Decimal('0.5'),
        decimal_places=1,
        required=False,
        label='Hodiny',
        widget=forms.NumberInput(attrs={'placeholder': 'Hodiny'}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = collaborator_queryset(user)

    def clean(self):
        cleaned_data = super().clean()
        filled = [cleaned_data.get('user'), cleaned_data.get('hours')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Vyplňte pracovníka a počet hodin, nebo řádek nechte prázdný.')
        return cleaned_data


WorkerHoursFormSet = forms.formset_factory(WorkerHoursForm, extra=3)


class TimeWorkedFilterForm(forms.Form):
    worker = forms.ModelChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=False,
        label='Pracovník',
        empty_label='Všichni pracovníci',
    )
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum od')
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum do')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and not user.is_manager_or_admin:
            # Workers only ever see their own hours, so picking someone else
            # would be a dead end.
            del self.fields['worker']

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get('date_from')
        date_to = cleaned_data.get('date_to')
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError('„Datum od“ musí být dřívější nebo stejné jako „datum do“.')
        return cleaned_data


class MachineFilterForm(forms.Form):
    """Filters the whole Stroje page — both the per-machine totals and the usage
    rows underneath them, which are two views of the same set of rows."""

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
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum od')
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum do')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and not user.is_manager_or_admin:
            # Workers only ever see their own machine usage, so this filter
            # would be a dead end.
            del self.fields['created_by']

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get('date_from')
        date_to = cleaned_data.get('date_to')
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError('„Datum od“ musí být dřívější nebo stejné jako „datum do“.')
        return cleaned_data


class JobFilterForm(forms.Form):
    """Filters for the manager dashboard. No worker variant: the whole view is
    manager/admin only, so nothing has to be hidden from anyone."""

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
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum od')
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Datum do')

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get('date_from')
        date_to = cleaned_data.get('date_to')
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError('„Datum od“ musí být dřívější nebo stejné jako „datum do“.')
        return cleaned_data
