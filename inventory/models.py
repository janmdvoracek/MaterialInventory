from django.conf import settings
from django.db import models


class StockMovement(models.Model):
    """Append-only ledger entry. Current stock is derived by summing quantity."""

    class MovementType(models.TextChoices):
        RECEIPT = "RECEIPT", "Příjem"
        SHIPMENT = "SHIPMENT", "Výdej"
        TRANSFORM_CONSUME = "TRANSFORM_CONSUME", "Zpracování – spotřeba"
        TRANSFORM_PRODUCE = "TRANSFORM_PRODUCE", "Zpracování – výroba"
        ADJUSTMENT = "ADJUSTMENT", "Ruční úprava"

    material = models.ForeignKey("materials.Material", on_delete=models.PROTECT, related_name="movements")
    location = models.ForeignKey("materials.Location", on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        help_text="Signed quantity: positive for stock in, negative for stock out.",
    )
    work_order = models.ForeignKey(
        "workorders.WorkOrder",
        on_delete=models.PROTECT,
        related_name="movements",
        null=True,
        blank=True,
    )
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_movements")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["material", "location"]),
        ]

    def __str__(self):
        return f"{self.movement_type}: {self.quantity} {self.material.unit_of_measure} of {self.material} @ {self.location}"
