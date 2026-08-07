from decimal import Decimal

from django.db.models import Sum

from materials.models import Material

from .models import StockMovement


def get_available_quantity(material, location, *, lock=False):
    """Sum of StockMovement.quantity for a material at a location.

    If lock=True, must be called inside transaction.atomic(); locks the
    Material row so concurrent writers for the same material serialize
    before reading/writing movements, closing the check-then-write race.
    """
    if lock:
        Material.objects.select_for_update().get(pk=material.pk)
    total = StockMovement.objects.filter(material=material, location=location).aggregate(total=Sum("quantity"))[
        "total"
    ]
    return total or Decimal("0")
