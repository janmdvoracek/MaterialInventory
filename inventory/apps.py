from django.apps import AppConfig


class InventoryConfig(AppConfig):
    """Migration history only — this app holds no code.

    `StockMovement` was its one model and now lives in `workorders`, where every
    line of code that touches a line item already was (moved in state only by
    `inventory.0008` / `workorders.0013`; the table keeps its `inventory_`
    name). The app stays in `INSTALLED_APPS` because `materials.0008` depends on
    `inventory.0006` to drop the `Location` FK column before the table it points
    at, and Django cannot resolve a dependency on an app it does not know about.

    No `verbose_name`: that only ever labelled the admin index section, and with
    nothing registered there is no section.
    """

    name = 'inventory'
