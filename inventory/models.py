from django.conf import settings
from django.db import models
from django.utils import timezone


class StockMovement(models.Model):
    """Append-only ledger entry. Current stock is derived by summing quantity."""

    class MovementType(models.TextChoices):
        RECEIPT = 'RECEIPT', 'Příjem'
        SHIPMENT = 'SHIPMENT', 'Výdej'
        TRANSFORM_CONSUME = 'TRANSFORM_CONSUME', 'Zpracování – spotřeba'
        TRANSFORM_PRODUCE = 'TRANSFORM_PRODUCE', 'Zpracování – výroba'
        ADJUSTMENT = 'ADJUSTMENT', 'Ruční úprava'

    material = models.ForeignKey(
        'materials.Material', on_delete=models.PROTECT, related_name='movements', verbose_name='materiál'
    )
    location = models.ForeignKey(
        'materials.Location', on_delete=models.PROTECT, related_name='movements', verbose_name='lokalita'
    )
    movement_type = models.CharField(max_length=20, choices=MovementType.choices, verbose_name='typ pohybu')
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        help_text='Množství se znaménkem: kladné pro příjem na sklad, záporné pro výdej ze skladu.',
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
    # When the movement physically happened. Defaults to now, but the entry
    # forms let a worker state a different time for something they are only
    # getting around to recording later — so this is *not* auto_now_add, and
    # everything that sorts, filters or exports the ledger keys off it.
    created_at = models.DateTimeField(default=timezone.now, verbose_name='datum a čas pohybu')
    # When the row was actually written. Untouchable, so a back-dated movement
    # still leaves a truthful audit trail of when it was entered.
    recorded_at = models.DateTimeField(auto_now_add=True, verbose_name='zaznamenáno')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='stock_movements', verbose_name='vytvořil'
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['material', 'location']),
        ]
        verbose_name = 'skladový pohyb'
        verbose_name_plural = 'skladové pohyby'

    def __str__(self):
        return f'{self.movement_type}: {self.quantity} {self.material.unit_of_measure} of {self.material} @ {self.location}'
