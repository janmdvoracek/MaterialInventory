import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


# Schema only. The fill-ups move in 0010 and the old column goes in 0011, so the
# data copy and the ALTERs never share a transaction (see 0008).
class Migration(migrations.Migration):
    dependencies = [
        ('machines', '0002_fuelsource'),
        ('workorders', '0008_remove_workorder_photo'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Refuelling',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='vytvořeno')),
                (
                    'performed_on',
                    models.DateField(default=django.utils.timezone.localdate, verbose_name='datum provedení'),
                ),
                ('notes', models.TextField(blank=True, verbose_name='poznámky')),
                (
                    'status',
                    models.CharField(
                        choices=[('PENDING', 'Čeká na schválení'), ('APPROVED', 'Schváleno')],
                        default='PENDING',
                        max_length=20,
                        verbose_name='stav',
                    ),
                ),
                ('reviewed_at', models.DateTimeField(blank=True, null=True, verbose_name='posouzeno')),
                (
                    'created_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='refuellings',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='vytvořil',
                    ),
                ),
                (
                    'reviewed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='reviewed_refuellings',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='posoudil',
                    ),
                ),
            ],
            options={
                'verbose_name': 'tankování',
                'verbose_name_plural': 'tankování',
                'ordering': ['-performed_on', '-created_at'],
            },
        ),
        migrations.AlterModelOptions(
            name='machinerefuel',
            options={
                'ordering': ['id'],
                'verbose_name': 'natankovaný stroj',
                'verbose_name_plural': 'natankované stroje',
            },
        ),
        migrations.AddField(
            model_name='machinerefuel',
            name='refuelling',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='machine_refuels',
                to='workorders.refuelling',
                verbose_name='tankování',
            ),
        ),
        migrations.AddField(
            model_name='machinerefuel',
            name='source',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='refuels',
                to='machines.fuelsource',
                verbose_name='zdroj',
            ),
        ),
        # Nullable for 0010, which detaches fill-ups before deleting fuel-only jobs.
        migrations.AlterField(
            model_name='machinerefuel',
            name='work_order',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='machine_refuels',
                to='workorders.workorder',
                verbose_name='zakázka',
            ),
        ),
    ]
