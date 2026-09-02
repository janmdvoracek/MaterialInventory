"""Adds the date a job was actually performed, and back-fills it.

`AddField` gives every existing row today's date, which would claim the whole
history happened on the day of the deploy. The back-fill immediately corrects
that to each job's own `created_at`: before this field existed there was no way
to record a job for any day but the one it was typed in, so that date *is* the
answer for them.
"""

import django.utils.timezone
from django.db import migrations, models
from django.db.models.functions import TruncDate


def backfill_from_created_at(apps, schema_editor):
    WorkOrder = apps.get_model('workorders', 'WorkOrder')
    # TruncDate converts to the current time zone (Europe/Prague), so a job
    # recorded at 00:30 local does not land on the previous UTC day.
    WorkOrder.objects.update(performed_on=TruncDate('created_at'))


class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0009_remove_returned_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='workorder',
            name='performed_on',
            field=models.DateField(default=django.utils.timezone.localdate, verbose_name='datum provedení'),
        ),
        migrations.RunPython(backfill_from_created_at, migrations.RunPython.noop),
    ]
