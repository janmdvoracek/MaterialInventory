"""Drops `StockMovement` from this app's state. The table is untouched.

The other half of the move is `workorders.0013`, which adds the same model to
that app's state with `db_table = 'inventory_stockmovement'` — so between the
two migrations the model changes app label and nothing else. No SQL runs.

What is left of `inventory` is its migration package, which has to stay:
`materials.0008` depends on `inventory.0006` to drop the `Location` FK column
before the table it points at, and Django cannot resolve a dependency on an app
that is not installed. The app therefore keeps its entry in `INSTALLED_APPS` and
holds no code.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0007_drop_unused_columns'),
        ('workorders', '0013_stockmovement'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name='StockMovement')],
        ),
    ]
