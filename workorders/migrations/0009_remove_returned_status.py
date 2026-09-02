from django.db import migrations, models


def returned_back_to_pending(apps, schema_editor):
    """RETURNED no longer exists. A job that was sent back was never approved
    and is still waiting for a decision, so it belongs in the pending queue —
    the manager now corrects or deletes it instead of handing it back."""
    WorkOrder = apps.get_model('workorders', 'WorkOrder')
    WorkOrder.objects.filter(status='RETURNED').update(status='PENDING')


class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0008_workorder_review_note_workorder_reviewed_at_and_more'),
    ]

    operations = [
        migrations.RunPython(returned_back_to_pending, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='workorder',
            name='review_note',
        ),
        migrations.AlterField(
            model_name='workorder',
            name='status',
            field=models.CharField(
                choices=[('PENDING', 'Čeká na schválení'), ('APPROVED', 'Schváleno')],
                default='PENDING',
                max_length=20,
                verbose_name='stav',
            ),
        ),
    ]
