"""Deleting a job deletes its line items.

The FK was PROTECT, left over from when a line item was stock history. It made
the admin refuse to delete any job with materials on it; `job_delete` only
worked because it deleted the line items by hand first.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0013_stockmovement'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockmovement',
            name='work_order',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='movements',
                to='workorders.workorder',
                verbose_name='zakázka',
            ),
        ),
    ]
