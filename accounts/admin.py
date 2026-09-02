from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group

from .models import User

# Groups are Django's own bundles of model permissions, registered by
# django.contrib.auth's admin rather than by this project. Nothing here consults
# them: access is `User.role` plus role_required in the views, and everyone who
# can open the admin at all is a superuser (both flags are derived from the role
# in save_model below), so a superuser passes every permission check regardless.
# A group could therefore only ever be a no-op that someone mistook for access
# control, so the section is taken off the index. Nothing is deleted — the model
# and any rows in it stay exactly as they are.
admin.site.unregister(Group)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """`is_staff`/`is_superuser` are never edited directly here — they're derived
    from `role` in `save_model` below, same rule `seed_data` uses (ADMIN gets
    both, everyone else gets neither). Note this only applies to accounts
    managed through this admin: a `createsuperuser` bootstrap account has
    `role=WORKER` by default, so editing it here without also setting its role
    to Admin would strip its admin access.
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
    list_filter = ('role', 'is_active')
    filter_horizontal = ()

    def save_model(self, request, obj, form, change):
        is_admin = obj.role == User.Role.ADMIN
        obj.is_staff = is_admin
        obj.is_superuser = is_admin
        super().save_model(request, obj, form, change)
