from django.conf import settings
from django.db import models, transaction
from django.db.models import F

from materials.models import Machine


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

    def save(self, *args, **kwargs):
        """Keep Machine.total_hours in sync no matter how this row is written —
        the Transform form and direct admin edits both have to go through here."""
        is_new = self._state.adding
        with transaction.atomic():
            old = None if is_new else MachineUsage.objects.select_for_update().get(pk=self.pk)
            super().save(*args, **kwargs)
            if is_new:
                Machine.objects.filter(pk=self.machine_id).update(total_hours=F('total_hours') + self.hours)
            elif old.machine_id == self.machine_id:
                delta = self.hours - old.hours
                if delta:
                    Machine.objects.filter(pk=self.machine_id).update(total_hours=F('total_hours') + delta)
            else:
                Machine.objects.filter(pk=old.machine_id).update(total_hours=F('total_hours') - old.hours)
                Machine.objects.filter(pk=self.machine_id).update(total_hours=F('total_hours') + self.hours)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            Machine.objects.filter(pk=self.machine_id).update(total_hours=F('total_hours') - self.hours)
            super().delete(*args, **kwargs)
