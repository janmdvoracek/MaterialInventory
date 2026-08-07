from django.conf import settings
from django.db import models


class WorkOrder(models.Model):
    """Groups the stock movements produced by a single transformation job."""

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="work_orders")
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"WorkOrder #{self.pk} - {self.description or 'untitled'}"
