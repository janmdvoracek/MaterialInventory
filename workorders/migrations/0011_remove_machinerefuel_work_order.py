import django.db.models.deletion
from django.db import migrations, models


# Its own migration, after 0010 has moved every fill-up (see 0008 for why).
class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0010_move_fill_ups'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='machinerefuel',
            name='work_order',
        ),
        migrations.AlterField(
            model_name='machinerefuel',
            name='refuelling',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='machine_refuels',
                to='workorders.refuelling',
                verbose_name='tankování',
            ),
        ),
    ]
