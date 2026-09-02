from django.conf import settings
from django.db import models


class StockMovement(models.Model):
    """One material line item on a transformation job — what it consumed or produced.

    This is a record of what was processed, not a stock balance. Nothing sums
    these into an on-hand quantity and nothing checks sufficiency before
    writing one; the app tracks jobs, hours and machines, not inventory levels.

    A row has no timestamp of its own. Its date is the job's `performed_on`, and
    its position within the job is the order it was typed — which is what `id`
    ordering below gives.
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
        verbose_name='zakázka',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='stock_movements', verbose_name='vytvořil'
    )

    class Meta:
        # By insertion, which within a job is the order the rows were typed.
        ordering = ['id']
        indexes = [
            models.Index(fields=['material']),
        ]
        verbose_name = 'položka zpracování'
        verbose_name_plural = 'položky zpracování'

    def __str__(self):
        return f'{self.movement_type}: {self.quantity} t of {self.material}'
