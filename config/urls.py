from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, reverse_lazy

from accounts.forms import CzechAuthenticationForm, CzechPasswordChangeForm

# The rest of the admin chrome comes from Django's own `cs` catalogs via
# LANGUAGE_CODE; these three strings are ours, so they have to be set by hand.
admin.site.site_header = 'Petrokámen – správa'
admin.site.site_title = 'Petrokámen'
admin.site.index_title = 'Správa dat'

urlpatterns = [
    path('admin/', admin.site.urls),
    path(
        'login/',
        auth_views.LoginView.as_view(
            template_name='registration/login.html', authentication_form=CzechAuthenticationForm
        ),
        name='login',
    ),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path(
        'password-change/',
        auth_views.PasswordChangeView.as_view(
            template_name='registration/password_change_form.html',
            form_class=CzechPasswordChangeForm,
            success_url=reverse_lazy('password_change_done'),
        ),
        name='password_change',
    ),
    path(
        'password-change/done/',
        auth_views.PasswordChangeDoneView.as_view(template_name='registration/password_change_done.html'),
        name='password_change_done',
    ),
    path('', include('inventory.urls')),
    path('workorders/', include('workorders.urls')),
]
