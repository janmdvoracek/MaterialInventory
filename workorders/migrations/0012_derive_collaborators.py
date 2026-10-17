from django.db import migrations


def derive_collaborators(apps, schema_editor):
    """Rebuild every job's collaborators from its hours rows, as `WorkOrder.derive_collaborators` does.

    The admin used to edit the two apart, so a worker could have hours on a
    job without being its collaborator, and never see it on Souhrn.
    """
    WorkOrder = apps.get_model('workorders', 'WorkOrder')
    for order in WorkOrder.objects.order_by('pk'):
        order.collaborators.set(order.worker_hours.exclude(user=order.created_by_id).values_list('user', flat=True))


class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0011_remove_machinerefuel_work_order'),
    ]

    # Nothing to undo: the field is derived, and the drifted state isn't worth rebuilding.
    operations = [
        migrations.RunPython(derive_collaborators, migrations.RunPython.noop),
    ]
