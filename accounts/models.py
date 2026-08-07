from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        WORKER = "WORKER", "Worker"
        MANAGER = "MANAGER", "Manager"
        ADMIN = "ADMIN", "Admin"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.WORKER)

    def __str__(self):
        return self.get_full_name() or self.username
