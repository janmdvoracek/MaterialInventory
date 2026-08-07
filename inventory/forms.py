from django import forms
from django.db.models import Sum

from accounts.models import User
from materials.models import Location, Material


class ReceiptForm(forms.Form):
    material = forms.ModelChoiceField(queryset=Material.objects.filter(is_active=True))
    location = forms.ModelChoiceField(queryset=Location.objects.filter(is_active=True))
    quantity = forms.DecimalField(min_value=0.001, max_digits=12, decimal_places=3)
    notes = forms.CharField(required=False, max_length=255)


class ShipmentForm(forms.Form):
    material = forms.ModelChoiceField(queryset=Material.objects.filter(is_active=True))
    location = forms.ModelChoiceField(queryset=Location.objects.filter(is_active=True))
    quantity = forms.DecimalField(min_value=0.001, max_digits=12, decimal_places=3)
    notes = forms.CharField(required=False, max_length=255)

    def clean(self):
        cleaned_data = super().clean()
        material = cleaned_data.get('material')
        location = cleaned_data.get('location')
        quantity = cleaned_data.get('quantity')
        if material and location and quantity:
            from .models import StockMovement

            available = StockMovement.objects.filter(material=material, location=location).aggregate(
                total=Sum('quantity')
            )['total'] or 0
            if quantity > available:
                raise forms.ValidationError(
                    f'Only {available} {material.unit_of_measure} of {material} available at {location}.'
                )
        return cleaned_data


class HistoryFilterForm(forms.Form):
    material = forms.ModelChoiceField(queryset=Material.objects.all().order_by('name'), required=False)
    location = forms.ModelChoiceField(queryset=Location.objects.all().order_by('name'), required=False)
    movement_type = forms.ChoiceField(required=False)
    created_by = forms.ModelChoiceField(
        queryset=User.objects.all().order_by('username'), required=False, label='Created by'
    )
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import StockMovement

        self.fields['movement_type'].choices = [('', 'All types')] + StockMovement.MovementType.choices

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get('date_from')
        date_to = cleaned_data.get('date_to')
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError('"Date from" must be on or before "date to".')
        return cleaned_data


class AdjustmentForm(forms.Form):
    DIRECTION_CHOICES = [('INCREASE', 'Increase stock'), ('DECREASE', 'Decrease stock')]

    material = forms.ModelChoiceField(queryset=Material.objects.filter(is_active=True))
    location = forms.ModelChoiceField(queryset=Location.objects.filter(is_active=True))
    direction = forms.ChoiceField(choices=DIRECTION_CHOICES)
    quantity = forms.DecimalField(min_value=0.001, max_digits=12, decimal_places=3)
    notes = forms.CharField(max_length=255, help_text='Reason for this adjustment (required for audit).')

    def clean(self):
        cleaned_data = super().clean()
        material = cleaned_data.get('material')
        location = cleaned_data.get('location')
        direction = cleaned_data.get('direction')
        quantity = cleaned_data.get('quantity')
        if material and location and quantity and direction == 'DECREASE':
            from .models import StockMovement

            available = StockMovement.objects.filter(material=material, location=location).aggregate(
                total=Sum('quantity')
            )['total'] or 0
            if quantity > available:
                raise forms.ValidationError(
                    f'Only {available} {material.unit_of_measure} of {material} available at {location}.'
                )
        return cleaned_data
