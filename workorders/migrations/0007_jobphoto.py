import django.db.models.deletion
from django.db import migrations, models

import workorders.models


def copy_photos_forward(apps, schema_editor):
    """Each job's one stored photo becomes its first `JobPhoto`. The file stays put."""
    WorkOrder = apps.get_model('workorders', 'WorkOrder')
    JobPhoto = apps.get_model('workorders', 'JobPhoto')
    JobPhoto.objects.bulk_create(
        JobPhoto(work_order_id=pk, file=name)
        for pk, name in WorkOrder.objects.exclude(photo='').values_list('pk', 'photo').order_by('pk')
    )


def copy_photos_back(apps, schema_editor):
    """The first photo goes back on the column; any others are left as files with no row."""
    WorkOrder = apps.get_model('workorders', 'WorkOrder')
    JobPhoto = apps.get_model('workorders', 'JobPhoto')
    for photo in JobPhoto.objects.order_by('-id'):
        WorkOrder.objects.filter(pk=photo.work_order_id).update(photo=photo.file.name)


class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0006_machineusage_hours_optional'),
    ]

    operations = [
        migrations.CreateModel(
            name='JobPhoto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to=workorders.models.job_photo_path, verbose_name='soubor')),
                (
                    'work_order',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='photos',
                        to='workorders.workorder',
                        verbose_name='zakázka',
                    ),
                ),
            ],
            options={
                'verbose_name': 'fotka',
                'verbose_name_plural': 'fotky',
                'ordering': ['id'],
            },
        ),
        migrations.RunPython(copy_photos_forward, copy_photos_back),
    ]
