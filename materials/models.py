from decimal import Decimal

from django.db import models


class Location(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Material(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    unit_of_measure = models.CharField(max_length=20, help_text="e.g. kg, m, pcs")
    category = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    track_stock = models.BooleanField(
        default=True,
        help_text=(
            "If off, this material can be shipped/consumed/decreased without a stock-sufficiency check "
            "(e.g. an on-site resource like excavated soil that's never formally received)."
        ),
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.sku})"


class Machine(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    total_hours = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    hourly_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Kč/hour, for reference only."
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
