"""Drops the line-item columns nothing reads, and the rows that needed them.

`notes` was never written by any form and no page showed it. `created_at` and
`recorded_at` were a pair of timestamps on a row that already takes its date
from its job's `performed_on`; nothing overrode the first and nothing read the
second.

The two RECEIPT/SHIPMENT rows left over from stock tracking go with them. They
were the only movements without a job, so `work_order` can stop being nullable —
it was only ever nullable for their sake.

All of this discards recorded values. That is the point: they were never read.
"""

from django.db import migrations, models
import django.db.models.deletion


def delete_rows_from_before_the_app_recorded_jobs(apps, schema_editor):
    StockMovement = apps.get_model('inventory', 'StockMovement')
    StockMovement.objects.filter(work_order__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0006_remove_stockmovement_location'),
        ('workorders', '0011_alter_workorder_options'),
    ]

    operations = [
        migrations.RunPython(delete_rows_from_before_the_app_recorded_jobs, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name='stockmovement',
            options={
                'ordering': ['id'],
                'verbose_name': 'položka zpracování',
                'verbose_name_plural': 'položky zpracování',
            },
        ),
        migrations.RemoveField(model_name='stockmovement', name='notes'),
        migrations.RemoveField(model_name='stockmovement', name='created_at'),
        migrations.RemoveField(model_name='stockmovement', name='recorded_at'),
        migrations.AlterField(
            model_name='stockmovement',
            name='work_order',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='movements',
                to='workorders.workorder',
                verbose_name='zakázka',
            ),
        ),
    ]
