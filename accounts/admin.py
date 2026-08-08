from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


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
                "classes": ("wide",),
                "fields": ("username", "usable_password", "password1", "password2", "role"),
            },
        ),
    )
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Role", {"fields": ("role", "is_active")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    list_display = ("username", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active")
    filter_horizontal = ()

    def save_model(self, request, obj, form, change):
        is_admin = obj.role == User.Role.ADMIN
        obj.is_staff = is_admin
        obj.is_superuser = is_admin
        super().save_model(request, obj, form, change)
