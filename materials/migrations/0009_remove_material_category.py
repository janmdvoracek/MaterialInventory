"""Drops the material category. The recorded values go with the column."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('materials', '0008_delete_location'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='material',
            name='category',
        ),
    ]
