"""Drops the running motohodiny counter on Machine.

No page read it — Stroje sums the approved usage rows instead, which is the only
figure that respects the review status and the page's filter. Keeping it meant
`MachineUsage.save()`/`delete()` maintaining it behind a row lock, and a
standing rule never to bulk-write or cascade-delete a usage row. All of that
goes with the column.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('materials', '0010_remove_material_unit_of_measure'),
    ]

    operations = [
        migrations.RemoveField(model_name='machine', name='total_hours'),
    ]
