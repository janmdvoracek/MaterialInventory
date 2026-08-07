from django import forms
from django.db.models import Sum

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
