"""Adopts `StockMovement` into `workorders`, in state only.

The model was the whole of the `inventory` app, which stopped being an app when
stock tracking was removed: no views, no forms, no URLs, no admin, one model
that only ever exists as a line item of a job in *this* app. Every line of code
that writes or reads one was already here.

Nothing happens to the database. This migration and `inventory.0008` are a
`SeparateDatabaseAndState` pair — one adds the model to this app's state, the
other drops it from `inventory`'s — and the table keeps the name it was created
under (`inventory_stockmovement`, pinned by `Meta.db_table`). Renaming it would
be a data migration bought for nothing, and `inventory` keeps its migration
package because `materials.0008` depends on `inventory.0006`.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('materials', '0011_remove_machine_total_hours'),
        ('inventory', '0007_drop_unused_columns'),
        ('workorders', '0012_drop_row_timestamps'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='StockMovement',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        (
                            'movement_type',
                            models.CharField(
                                choices=[
                                    ('TRANSFORM_CONSUME', 'Zpracování – spotřeba'),
                                    ('TRANSFORM_PRODUCE', 'Zpracování – výroba'),
                                ],
                                max_length=20,
                                verbose_name='typ pohybu',
                            ),
                        ),
                        (
                            'quantity',
                            models.DecimalField(
                                decimal_places=3,
                                help_text='Množství se znaménkem: kladné pro vyrobený materiál, záporné pro spotřebovaný.',
                                max_digits=12,
                                verbose_name='množství',
                            ),
                        ),
                        (
                            'created_by',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name='stock_movements',
                                to=settings.AUTH_USER_MODEL,
                                verbose_name='vytvořil',
                            ),
                        ),
                        (
                            'material',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name='movements',
                                to='materials.material',
                                verbose_name='materiál',
                            ),
                        ),
                        (
                            'work_order',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name='movements',
                                to='workorders.workorder',
                                verbose_name='zakázka',
                            ),
                        ),
                    ],
                    options={
                        'verbose_name': 'položka zpracování',
                        'verbose_name_plural': 'položky zpracování',
                        'db_table': 'inventory_stockmovement',
                        'ordering': ['id'],
                        'indexes': [models.Index(fields=['material'], name='inventory_s_materia_5d3f74_idx')],
                    },
                ),
            ],
        ),
    ]
