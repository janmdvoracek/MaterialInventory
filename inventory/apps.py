from django.apps import AppConfig


class InventoryConfig(AppConfig):
    name = 'inventory'
    # No longer a warehouse: this app holds the material line items of a
    # transformation job, not stock levels.
    verbose_name = 'Zpracování'
