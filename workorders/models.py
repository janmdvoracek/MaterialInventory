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


class MachineUsage(models.Model):
    """One machine's hours logged against a single transformation job."""

    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name="machine_usages")
    machine = models.ForeignKey("materials.Machine", on_delete=models.PROTECT, related_name="usages")
    hours = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.machine} - {self.hours}h (WorkOrder #{self.work_order_id})"
