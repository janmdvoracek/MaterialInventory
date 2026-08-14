from django import forms
from django.db.models import Sum

from accounts.models import User
from materials.models import Location, Material


class ReceiptForm(forms.Form):
    material = forms.ModelChoiceField(
        queryset=Material.objects.filter(is_active=True), label='Materiál', empty_label='Vyberte materiál'
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True), label='Lokalita', empty_label='Vyberte lokalitu'
    )
    quantity = forms.DecimalField(min_value=0.001, max_digits=12, decimal_places=3, label='Množství')
    notes = forms.CharField(required=False, max_length=255, label='Poznámka')


class ShipmentForm(forms.Form):
    material = forms.ModelChoiceField(
        queryset=Material.objects.filter(is_active=True), label='Materiál', empty_label='Vyberte materiál'
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True), label='Lokalita', empty_label='Vyberte lokalitu'
    )
    quantity = forms.DecimalField(min_value=0.001, max_digits=12, decimal_places=3, label='Množství')
    notes = forms.CharField(required=False, max_length=255, label='Poznámka')

    def clean(self):
        cleaned_data = super().clean()
        material = cleaned_data.get('material')
        location = cleaned_data.get('location')
        quantity = cleaned_data.get('quantity')
        if material and location and quantity and material.track_stock:
            from .models import StockMovement

            available = (
                StockMovement.objects.filter(material=material, location=location).aggregate(total=Sum('quantity'))[
                    'total'
                ]
                or 0
            )
            if quantity > available:
                raise forms.ValidationError(
                    f'K dispozici je pouze {available} {material.unit_of_measure} materiálu {material} na lokalitě {location}.'
                )
        return cleaned_data


class HistoryFilterForm(forms.Form):
    # On the filter forms the blank option means "don't filter by this", so it
    # reads as "all ...", matching the movement_type choices set up in __init__.
    material = forms.ModelChoiceField(
        queryset=Material.objects.all().order_by('name'),
        required=False,
        label='Materiál',
        empty_label='Všechny materiály',
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by('name'),
        required=False,
        label='Lokalita',
        empty_label='Všechny lokality',
    )
    movement_type = forms.ChoiceField(required=False, label='Typ pohybu')
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
        from .models import StockMovement

        self.fields['movement_type'].choices = [('', 'Všechny typy')] + StockMovement.MovementType.choices
        if user is not None and not user.is_manager_or_admin:
            # Plain workers only ever see their own movements (enforced in the
            # view too), so letting them pick someone else here would be a
            # no-op at best and a confusing dead end at worst.
            del self.fields['created_by']

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get('date_from')
        date_to = cleaned_data.get('date_to')
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError('„Datum od“ musí být dřívější nebo stejné jako „datum do“.')
        return cleaned_data


class AdjustmentForm(forms.Form):
    DIRECTION_CHOICES = [('INCREASE', 'Navýšit sklad'), ('DECREASE', 'Snížit sklad')]

    material = forms.ModelChoiceField(
        queryset=Material.objects.filter(is_active=True), label='Materiál', empty_label='Vyberte materiál'
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True), label='Lokalita', empty_label='Vyberte lokalitu'
    )
    direction = forms.ChoiceField(choices=DIRECTION_CHOICES, label='Směr')
    quantity = forms.DecimalField(min_value=0.001, max_digits=12, decimal_places=3, label='Množství')
    notes = forms.CharField(max_length=255, label='Poznámka', help_text='Důvod této úpravy (povinné pro audit).')

    def clean(self):
        cleaned_data = super().clean()
        material = cleaned_data.get('material')
        location = cleaned_data.get('location')
        direction = cleaned_data.get('direction')
        quantity = cleaned_data.get('quantity')
        if material and location and quantity and direction == 'DECREASE' and material.track_stock:
            from .models import StockMovement

            available = (
                StockMovement.objects.filter(material=material, location=location).aggregate(total=Sum('quantity'))[
                    'total'
                ]
                or 0
            )
            if quantity > available:
                raise forms.ValidationError(
                    f'K dispozici je pouze {available} {material.unit_of_measure} materiálu {material} na lokalitě {location}.'
                )
        return cleaned_data
