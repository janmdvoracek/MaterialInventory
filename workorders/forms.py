from decimal import Decimal

from django import forms

from accounts.models import User
from materials.models import Location, Machine, Material


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


class MovementItemForm(forms.Form):
    # required=False because a whole row may be left blank, but these are still
    # entry fields — the blank option is a prompt, not an "all" filter.
    material = forms.ModelChoiceField(
        queryset=Material.objects.filter(is_active=True),
        required=False,
        label='Materiál',
        empty_label='Materiál',
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True),
        required=False,
        label='Lokalita',
        empty_label='Lokalita',
    )
    quantity = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=7,
        decimal_places=2,
        required=False,
        label='Množství',
        widget=forms.NumberInput(attrs={'placeholder': 'Množství'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        filled = [cleaned_data.get('material'), cleaned_data.get('location'), cleaned_data.get('quantity')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Vyplňte materiál, lokalitu a množství, nebo řádek nechte prázdný.')
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

    def clean(self):
        cleaned_data = super().clean()
        filled = [cleaned_data.get('machine'), cleaned_data.get('hours')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Vyplňte stroj a počet hodin, nebo řádek nechte prázdný.')
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


class MachineHistoryFilterForm(forms.Form):
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
            # Same restriction as the stock movement history: workers only ever
            # see their own machine usage, so this filter would be a dead end.
            del self.fields['created_by']

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get('date_from')
        date_to = cleaned_data.get('date_to')
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError('„Datum od“ musí být dřívější nebo stejné jako „datum do“.')
        return cleaned_data
