from django.conf import settings
from django.db import models, transaction
from django.db.models import F

from materials.models import Machine


class WorkOrder(models.Model):
    """Groups the stock movements produced by a single transformation job.

    A job a worker submits is a *proposal* until a manager or admin approves it:
    only APPROVED jobs are counted by the Hodiny, Stroje and machine-history
    pages. A manager's own submission is approved on the spot — there is nobody
    above them to sign it off.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Čeká na schválení'
        APPROVED = 'APPROVED', 'Schváleno'
        RETURNED = 'RETURNED', 'Vráceno k přepracování'

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='vytvořeno')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='work_orders', verbose_name='vytvořil'
    )
    collaborators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='collaborated_work_orders',
        blank=True,
        verbose_name='spolupracovníci',
    )
    description = models.CharField(max_length=255, blank=True, verbose_name='popis')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name='stav')
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='posouzeno')
    # Null for jobs approved by the backfill migration — they predate the rule
    # and were never signed off by a person.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reviewed_work_orders',
        null=True,
        blank=True,
        verbose_name='posoudil',
    )
    review_note = models.CharField(max_length=255, blank=True, verbose_name='poznámka k posouzení')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'zakázka'
        verbose_name_plural = 'zakázky'

    def __str__(self):
        return f'WorkOrder #{self.pk} - {self.description or "untitled"}'

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED


class WorkerHours(models.Model):
    """Labour hours one person spent on a transformation job.

    Typed on the Transform form — one row for the person recording the job and
    one for each collaborator they named. This is what the Hodiny tab reports;
    `MachineUsage.hours` is machine runtime and is a separate number entirely.
    """

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name='worker_hours', verbose_name='zakázka'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='worked_hours', verbose_name='pracovník'
    )
    hours = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='hodiny')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='vytvořeno')

    class Meta:
        ordering = ['user__username']
        constraints = [
            models.UniqueConstraint(fields=['work_order', 'user'], name='unique_worker_hours_per_work_order')
        ]
        verbose_name = 'odpracované hodiny'
        verbose_name_plural = 'odpracované hodiny'

    def __str__(self):
        return f'{self.user} - {self.hours}h (WorkOrder #{self.work_order_id})'


class MachineUsage(models.Model):
    """One machine's hours and processed tonnage on a single transformation job."""

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name='machine_usages', verbose_name='zakázka'
    )
    machine = models.ForeignKey(
        'materials.Machine', on_delete=models.PROTECT, related_name='usages', verbose_name='stroj'
    )
    hours = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='motohodiny')
    # Nullable only because rows written before the column existed have no
    # answer — unknown, not zero. The Transform form requires it on every row
    # it writes, alongside the machine and its hours.
    tons = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='odpracované tuny')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='vytvořeno')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'využití stroje'
        verbose_name_plural = 'využití strojů'

    def __str__(self):
        return f'{self.machine} - {self.hours}h (WorkOrder #{self.work_order_id})'

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
