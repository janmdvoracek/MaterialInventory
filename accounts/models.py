from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        WORKER = 'WORKER', 'Worker'
        MANAGER = 'MANAGER', 'Manager'
        ADMIN = 'ADMIN', 'Admin'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.WORKER)

    @property
    def is_manager_or_admin(self):
        # Superusers count regardless of role: `createsuperuser` never sets one,
        # so they'd otherwise fall back to WORKER and lose privileged UI.
        return self.is_superuser or self.role in (self.Role.MANAGER, self.Role.ADMIN)

    def __str__(self):
        return self.get_full_name() or self.username
