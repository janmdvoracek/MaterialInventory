"""Drops the per-row timestamps on the hours and machine-usage rows.

Neither was ever read outside the admin. A row belongs to a job, and the job's
`performed_on` is its date; `-id` is enough to break ties within one.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0011_alter_workorder_options'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='machineusage',
            options={'ordering': ['-id'], 'verbose_name': 'využití stroje', 'verbose_name_plural': 'využití strojů'},
        ),
        migrations.RemoveField(model_name='workerhours', name='created_at'),
        migrations.RemoveField(model_name='machineusage', name='created_at'),
    ]
