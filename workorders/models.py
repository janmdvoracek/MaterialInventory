from uuid import uuid4

from django.conf import settings
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone


def job_photo_path(instance, filename):
    """`job_photos/<upload year>/<month>/<uuid>.<ext>` under MEDIA_ROOT.

    A uuid because phone file names collide. Upload month rather than
    `performed_on`, which an edit can move while the file can't.
    """
    suffix = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'jpg'
    return f'job_photos/{timezone.localdate():%Y/%m}/{uuid4().hex}.{suffix}'


def discard_photo(photo):
    """Delete a stored photo's file once the current transaction commits.

    On commit, because an orphaned file is harmless while a row pointing at a
    deleted file is not. Called by `_delete_photo_file` and `WorkOrderAdmin.save_formset`.
    """
    if not photo:
        return
    # Read off the FieldFile now; the attribute it came from is about to change.
    storage, name = photo.storage, photo.name
    transaction.on_commit(lambda: storage.delete(name))


class ReviewStatus(models.TextChoices):
    """Shared by both kinds of entry: a job and a refuelling."""

    PENDING = 'PENDING', 'Čeká na schválení'
    APPROVED = 'APPROVED', 'Schváleno'


class WorkOrder(models.Model):
    """One transformation job.

    Only APPROVED jobs count in reports. A manager's own submission is approved
    on the spot; a job is never handed back, only edited or deleted.
    """

    Status = ReviewStatus

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
    # A label, not a stock location. Null only on jobs recorded before the
    # column existed; the form requires it.
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='work_orders',
        null=True,
        blank=True,
        verbose_name='lokace',
    )
    description = models.CharField(max_length=255, verbose_name='popis')
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

    def derive_collaborators(self):
        """Set `collaborators` to everyone with hours on the job except its author.

        Derived, never entered: the job form and the admin both call this
        after writing the hours rows, so the two can't disagree.
        """
        self.collaborators.set(self.worker_hours.exclude(user=self.created_by_id).values_list('user', flat=True))


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
    """One machine on a job: its hour-meter reading and the tonnage it processed."""

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name='machine_usages', verbose_name='zakázka'
    )
    machine = models.ForeignKey(
        'machines.Machine', on_delete=models.PROTECT, related_name='usages', verbose_name='stroj'
    )
    # Null means not recorded (unknown), not zero. `hours` is the meter reading.
    hours = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='motohodiny')
    tons = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='odpracované tuny')

    class Meta:
        ordering = ['-id']
        verbose_name = 'využití stroje'
        verbose_name_plural = 'využití strojů'

    def __str__(self):
        hours = '?' if self.hours is None else self.hours
        return f'{self.machine} - {hours}h (WorkOrder #{self.work_order_id})'


class Refuelling(models.Model):
    """One refuelling entry: fuel put into one or more machines, recorded apart from any job.

    Reviewed like a job (`ReviewStatus`); only APPROVED entries count on Stroje.
    """

    Status = ReviewStatus

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='vytvořeno')
    performed_on = models.DateField(default=timezone.localdate, verbose_name='datum provedení')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='refuellings', verbose_name='vytvořil'
    )
    notes = models.TextField(blank=True, verbose_name='poznámky')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name='stav')
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='posouzeno')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reviewed_refuellings',
        null=True,
        blank=True,
        verbose_name='posoudil',
    )

    class Meta:
        ordering = ['-performed_on', '-created_at']
        verbose_name = 'tankování'
        verbose_name_plural = 'tankování'

    def __str__(self):
        return f'Refuelling #{self.pk} ({self.performed_on})'

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED


class MachineRefuel(models.Model):
    """Fuel put into one machine on a refuelling entry, in litres."""

    refuelling = models.ForeignKey(
        Refuelling, on_delete=models.CASCADE, related_name='machine_refuels', verbose_name='tankování'
    )
    machine = models.ForeignKey(
        'machines.Machine', on_delete=models.PROTECT, related_name='refuels', verbose_name='stroj'
    )
    # Optional: a fill-up may not say where its fuel came from.
    source = models.ForeignKey(
        'machines.FuelSource',
        on_delete=models.PROTECT,
        related_name='refuels',
        null=True,
        blank=True,
        verbose_name='zdroj',
    )
    litres = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='natankované litry')

    class Meta:
        # The order the rows were typed.
        ordering = ['id']
        verbose_name = 'natankovaný stroj'
        verbose_name_plural = 'natankované stroje'

    def __str__(self):
        return f'{self.machine} - {self.litres} l (Refuelling #{self.refuelling_id})'


class JobPhoto(models.Model):
    """One photo attached to a job (up to `forms.PHOTO_MAX_COUNT`).

    A FileField, not an ImageField: the form already decodes and shrinks every
    upload. Served only through `protected_media`.
    """

    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='photos', verbose_name='zakázka')
    file = models.FileField(upload_to=job_photo_path, verbose_name='soubor')

    class Meta:
        # The order they were added.
        ordering = ['id']
        verbose_name = 'fotka'
        verbose_name_plural = 'fotky'

    def __str__(self):
        return f'Fotka #{self.pk} (WorkOrder #{self.work_order_id})'


# Django never deletes a FileField's file. A signal covers every path (views,
# cascades, admin bulk delete); registering it also disables fast-delete for the cascade.
@receiver(post_delete, sender=JobPhoto)
def _delete_photo_file(sender, instance, **kwargs):
    discard_photo(instance.file)
