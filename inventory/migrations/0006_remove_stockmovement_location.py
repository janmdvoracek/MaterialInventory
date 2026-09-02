"""Drops the location column from every job line item.

Locations were removed from the app entirely; this deletes the recorded value,
which cannot be recovered from anything else in the database. The
`material`+`location` index goes with it, narrowed to `material` alone.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_alter_stockmovement_options_and_more'),
        ('materials', '0007_machine_rate_per_ton'),
        ('workorders', '0009_remove_returned_status'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='stockmovement',
            name='inventory_s_materia_9b079e_idx',
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(fields=['material'], name='inventory_s_materia_5d3f74_idx'),
        ),
        migrations.RemoveField(
            model_name='stockmovement',
            name='location',
        ),
    ]
