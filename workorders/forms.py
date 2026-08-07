from django import forms

from materials.models import Location, Material


class WorkOrderForm(forms.Form):
    description = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={'placeholder': 'e.g. Cut steel bars into brackets'}))


class MovementItemForm(forms.Form):
    material = forms.ModelChoiceField(queryset=Material.objects.filter(is_active=True), required=False)
    location = forms.ModelChoiceField(queryset=Location.objects.filter(is_active=True), required=False)
    quantity = forms.DecimalField(min_value=0.001, max_digits=12, decimal_places=3, required=False)

    def clean(self):
        cleaned_data = super().clean()
        filled = [cleaned_data.get('material'), cleaned_data.get('location'), cleaned_data.get('quantity')]
        if any(filled) and not all(filled):
            raise forms.ValidationError('Fill in material, location and quantity, or leave the row empty.')
        return cleaned_data


ConsumedFormSet = forms.formset_factory(MovementItemForm, extra=3)
ProducedFormSet = forms.formset_factory(MovementItemForm, extra=3)
