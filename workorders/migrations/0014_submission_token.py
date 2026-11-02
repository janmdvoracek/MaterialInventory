from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workorders', '0013_revision'),
    ]

    operations = [
        migrations.AddField(
            model_name='refuelling',
            name='submission_token',
            field=models.UUIDField(blank=True, editable=False, null=True, unique=True, verbose_name='token odeslání'),
        ),
        migrations.AddField(
            model_name='workorder',
            name='submission_token',
            field=models.UUIDField(blank=True, editable=False, null=True, unique=True, verbose_name='token odeslání'),
        ),
    ]
