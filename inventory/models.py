from django.conf import settings
from django.db import models
from django.utils import timezone


class StockMovement(models.Model):
    """One material line item on a transformation job — what it consumed or produced.

    This is a record of what was processed, not a stock balance. Nothing sums
    these into an on-hand quantity and nothing checks sufficiency before
    writing one; the app tracks jobs, hours and machines, not inventory levels.

    Rows written before stock tracking was removed may still carry the retired
    RECEIPT / SHIPMENT / ADJUSTMENT values, which are no longer in `MovementType`
    and no longer render a Czech label. They are left in place deliberately.
    """

    class MovementType(models.TextChoices):
        TRANSFORM_CONSUME = 'TRANSFORM_CONSUME', 'Zpracování – spotřeba'
        TRANSFORM_PRODUCE = 'TRANSFORM_PRODUCE', 'Zpracování – výroba'

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
        'workorders.WorkOrder',
        on_delete=models.PROTECT,
        related_name='movements',
        null=True,
        blank=True,
        verbose_name='zakázka',
    )
    notes = models.CharField(max_length=255, blank=True, verbose_name='poznámka')
    # Left as default=timezone.now rather than auto_now_add: the back-dating
    # checkbox that used to override it lived on the removed Příjem/Výdej forms,
    # so nothing writes a value here today and this always equals recorded_at.
    # Kept overridable so a job can be back-dated later without a schema change.
    created_at = models.DateTimeField(default=timezone.now, verbose_name='datum a čas pohybu')
    recorded_at = models.DateTimeField(auto_now_add=True, verbose_name='zaznamenáno')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='stock_movements', verbose_name='vytvořil'
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['material']),
        ]
        verbose_name = 'položka zpracování'
        verbose_name_plural = 'položky zpracování'

    def __str__(self):
        return f'{self.movement_type}: {self.quantity} t of {self.material}'
