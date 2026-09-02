"""Orders jobs by the day the work happened, then by entry order within a day.

Everything user-facing switched to `performed_on` at the same time, so the
model default and the lists agree.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0010_workorder_performed_on'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='workorder',
            options={
                'ordering': ['-performed_on', '-created_at'],
                'verbose_name': 'zakázka',
                'verbose_name_plural': 'zakázky',
            },
        ),
    ]
