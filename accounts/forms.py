from django import forms
from django.contrib.auth.forms import AuthenticationForm


class CzechAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label='Uživatelské jméno', widget=forms.TextInput(attrs={'autofocus': True}))
    password = forms.CharField(label='Heslo', strip=False, widget=forms.PasswordInput)

    error_messages = {
        'invalid_login': (
            'Zadejte prosím správné uživatelské jméno a heslo. Pozor, u obou polí záleží na velikosti písmen.'
        ),
        'inactive': 'Tento účet je neaktivní.',
    }
