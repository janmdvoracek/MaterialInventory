from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group

from .models import User

# Access is `role` + role_required and every admin user is a superuser, so
# groups would do nothing. Unregistered only; no data is deleted.
admin.site.unregister(Group)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """`is_staff`/`is_superuser` are derived from `role` in `save_model` (ADMIN gets both).

    A `createsuperuser` account defaults to WORKER, so saving it here without
    setting the role strips its admin access.
    """

    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': ('username', 'usable_password', 'password1', 'password2', 'role'),
            },
        ),
    )
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Osobní údaje', {'fields': ('first_name', 'last_name', 'email')}),
        ('Role', {'fields': ('role', 'is_active')}),
        ('Důležitá data', {'fields': ('last_login', 'date_joined')}),
    )
    list_display = ('username', 'first_name', 'last_name', 'role', 'is_active')
    list_editable = ('is_active',)
    list_filter = ('role', 'is_active')
    filter_horizontal = ()

    # Nobody can deactivate their own account. A disabled field also ignores a forged POST.
    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if obj is not None and obj.pk == request.user.pk:
            return (*readonly, 'is_active')
        return readonly

    def get_changelist_form(self, request, **kwargs):
        base = super().get_changelist_form(request, **kwargs)

        class ChangelistForm(base):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                if self.instance.pk == request.user.pk and 'is_active' in self.fields:
                    self.fields['is_active'].disabled = True

        return ChangelistForm

    def save_model(self, request, obj, form, change):
        is_admin = obj.role == User.Role.ADMIN
        obj.is_staff = is_admin
        obj.is_superuser = is_admin
        super().save_model(request, obj, form, change)
