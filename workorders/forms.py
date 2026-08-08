from django import forms

from materials.models import Location, Machine, Material


class WorkOrderForm(forms.Form):
    description = forms.CharField(
        required=False,
        max_length=255,
        label='Popis',
        widget=forms.TextInput(attrs={'placeholder': 'např. Řezání ocelových tyčí na konzoly'}),
    )


class MovementItemForm(forms.Form):
    material = forms.ModelChoiceField(
        queryset=Material.objects.filter(is_active=True), required=False, label='Materiál'
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True), required=False, label='Lokalita'
    )
    quantity = forms.DecimalField(min_value=0.001, max_digits=12, decimal_places=3, required=False, label='Množství')

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
        queryset=Machine.objects.filter(is_active=True), required=False, label='Stroj'
    )
    hours = forms.DecimalField(min_value=0.01, max_digits=12, decimal_places=2, required=False, label='Hodiny')

    def clean(self):
        cleaned_data = super().clean()
        filled = [cleaned_data.get('machine'), cleaned_data.get('hours')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Vyplňte stroj a počet hodin, nebo řádek nechte prázdný.')
        return cleaned_data


MachineUsageFormSet = forms.formset_factory(MachineUsageForm, extra=3)
