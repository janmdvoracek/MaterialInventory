from django.conf import settings
from django.db import models
from django.utils import timezone


class WorkOrder(models.Model):
    """One transformation job.

    Only APPROVED jobs count in reports. A manager's own submission is approved
    on the spot; a job is never handed back, only edited or deleted.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Čeká na schválení'
        APPROVED = 'APPROVED', 'Schváleno'

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='vytvořeno')
    # When the work was done; `created_at` is when it was typed in.
    performed_on = models.DateField(default=timezone.localdate, verbose_name='datum provedení')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='work_orders', verbose_name='vytvořil'
    )
    collaborators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='collaborated_work_orders',
        blank=True,
        verbose_name='spolupracovníci',
    )
    # Where the work was done. Nullable for jobs recorded before the column
    # existed and for fuel-only jobs, which are not asked for one; required on
    # every other job by the view (see `WORK_FIELDS`). A label, not a stock
    # location: nothing is balanced or summed per location.
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='work_orders',
        null=True,
        blank=True,
        verbose_name='lokace',
    )
    description = models.CharField(max_length=255, verbose_name='popis')
    # Free-form and optional, unlike `description`: a whole paragraph, not a one-liner.
    notes = models.TextField(blank=True, verbose_name='poznámky')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name='stav')
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='posouzeno')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reviewed_work_orders',
        null=True,
        blank=True,
        verbose_name='posoudil',
    )

    class Meta:
        ordering = ['-performed_on', '-created_at']
        verbose_name = 'zakázka'
        verbose_name_plural = 'zakázky'

    def __str__(self):
        return f'WorkOrder #{self.pk} - {self.description or "untitled"}'

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED


class StockMovement(models.Model):
    """One material line item on a job: consumed (negative) or produced (positive).

    Not a stock balance. It has no timestamp; its date is the job's `performed_on`.
    """

    class MovementType(models.TextChoices):
        TRANSFORM_CONSUME = 'TRANSFORM_CONSUME', 'Spotřeba'
        TRANSFORM_PRODUCE = 'TRANSFORM_PRODUCE', 'Výroba'

    material = models.ForeignKey(
        'materials.Material', on_delete=models.PROTECT, related_name='movements', verbose_name='materiál'
    )
    movement_type = models.CharField(max_length=20, choices=MovementType.choices, verbose_name='typ pohybu')
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        help_text='Množství se znaménkem: kladné pro vyrobený materiál, záporné pro spotřebovaný.',
        verbose_name='množství',
    )
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name='movements',
        verbose_name='zakázka',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='stock_movements', verbose_name='vytvořil'
    )

    class Meta:
        # The order the rows were typed.
        ordering = ['id']
        indexes = [
            models.Index(fields=['material']),
        ]
        verbose_name = 'položka zpracování'
        verbose_name_plural = 'položky zpracování'

    def __str__(self):
        return f'{self.movement_type}: {self.quantity} t of {self.material}'


class WorkerHours(models.Model):
    """Labour hours one person worked on a job. Unrelated to `MachineUsage.hours`."""

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name='worker_hours', verbose_name='zakázka'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='worked_hours', verbose_name='pracovník'
    )
    hours = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='hodiny')

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
        'machines.Machine', on_delete=models.PROTECT, related_name='usages', verbose_name='stroj'
    )
    hours = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='motohodiny')
    # Null only on rows from before the column existed: unknown, not zero.
    tons = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='odpracované tuny')

    class Meta:
        ordering = ['-id']
        verbose_name = 'využití stroje'
        verbose_name_plural = 'využití strojů'

    def __str__(self):
        return f'{self.machine} - {self.hours}h (WorkOrder #{self.work_order_id})'


class MachineRefuel(models.Model):
    """Fuel put into one machine on a single job, in litres.

    Its own table rather than a column on `MachineUsage`, because the two are
    independent: a machine can be refuelled on a job that ran no hours at all —
    a job may be nothing but fill-ups — so a refuel row has to be able to exist
    with no usage row beside it. Litres are not part of the mass balance and no
    report derives them from hours or tonnage.

    Like `MachineUsage` and `StockMovement` it carries no timestamp of its own;
    its date is its job's `performed_on`, because `job_edit` rewrites every row.
    """

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name='machine_refuels', verbose_name='zakázka'
    )
    machine = models.ForeignKey(
        'machines.Machine', on_delete=models.PROTECT, related_name='refuels', verbose_name='stroj'
    )
    # Not nullable, unlike `MachineUsage.tons`: the column is new but a refuel
    # row only ever exists because someone typed a number into it, so there is
    # no "unknown" to represent and a zero total is a real zero.
    litres = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='natankované litry')

    class Meta:
        ordering = ['-id']
        verbose_name = 'tankování'
        verbose_name_plural = 'tankování'

    def __str__(self):
        return f'{self.machine} - {self.litres} l (WorkOrder #{self.work_order_id})'
