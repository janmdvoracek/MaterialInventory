from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        WORKER = 'WORKER', 'Pracovník'
        MANAGER = 'MANAGER', 'Vedoucí'
        ADMIN = 'ADMIN', 'Správce'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.WORKER, verbose_name='role')

    @property
    def is_manager_or_admin(self):
        # Superusers count regardless of role: `createsuperuser` never sets one,
        # so they'd otherwise fall back to WORKER and lose privileged UI.
        return self.is_superuser or self.role in (self.Role.MANAGER, self.Role.ADMIN)

    @property
    def has_admin_access(self):
        """May this user open /admin/ at all?

        Read by `accounts.middleware.AdminSessionRequiredMiddleware`, which 404s
        the admin for everyone else, *and* by the header link in `base.html`, so
        a visible „Administrace“ can never lead to a 404. `is_staff` is what
        Django itself requires; the rest is this app's rule that only ADMIN-role
        accounts administer it — superusers again excepted, since
        `createsuperuser` never sets a role.
        """
        return self.is_active and self.is_staff and (self.is_superuser or self.role == self.Role.ADMIN)

    def __str__(self):
        return self.get_full_name() or self.username
