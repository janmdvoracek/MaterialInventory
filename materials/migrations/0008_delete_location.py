"""Drops the Location table itself, once nothing points at it any more."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0006_remove_stockmovement_location'),
        ('materials', '0007_machine_rate_per_ton'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Location',
        ),
    ]
