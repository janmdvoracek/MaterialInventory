from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm


class CzechAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label='Uživatelské jméno', widget=forms.TextInput(attrs={'autofocus': True}))
    password = forms.CharField(label='Heslo', strip=False, widget=forms.PasswordInput)

    error_messages = {
        'invalid_login': (
            'Zadejte prosím správné uživatelské jméno a heslo. Pozor, u obou polí záleží na velikosti písmen.'
        ),
        'inactive': 'Tento účet je neaktivní.',
    }


class CzechPasswordChangeForm(PasswordChangeForm):
    error_messages = {
        **PasswordChangeForm.error_messages,
        'password_incorrect': 'Zadané současné heslo není správné.',
        'password_mismatch': 'Zadaná hesla se neshodují.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].label = 'Současné heslo'
        self.fields['old_password'].widget.attrs['autofocus'] = True
        self.fields['new_password1'].label = 'Nové heslo'
        self.fields['new_password2'].label = 'Potvrzení nového hesla'
