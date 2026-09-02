"""Drops the per-material unit. Every quantity in the app is tonnes now, so
the unit is a constant in the UI rather than a column."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('materials', '0009_remove_material_category'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='material',
            name='unit_of_measure',
        ),
    ]
