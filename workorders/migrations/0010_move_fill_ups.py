from django.db import migrations


def move_fill_ups(apps, schema_editor):
    """Give every job's fill-ups a refuelling entry of their own.

    The entry inherits the job's date, author and review state, so an approved
    fill-up stays counted and a pending one still waits. A fuel-only job (fill-ups
    and nothing else, not even a photo) is then empty and is deleted; its
    description and notes carry over. A job that also recorded work keeps
    everything else, and its entry says where it came from.
    """
    WorkOrder = apps.get_model('workorders', 'WorkOrder')
    Refuelling = apps.get_model('workorders', 'Refuelling')
    for order in WorkOrder.objects.filter(machine_refuels__isnull=False).distinct().order_by('pk'):
        fuel_only = not (
            order.movements.exists()
            or order.machine_usages.exists()
            or order.worker_hours.exists()
            or order.photos.exists()
        )
        if fuel_only:
            notes = '\n\n'.join(text for text in (order.description, order.notes) if text)
        else:
            notes = f'Převedeno ze zpracování #{order.pk} ({order.description}).'
        entry = Refuelling.objects.create(
            performed_on=order.performed_on,
            created_by_id=order.created_by_id,
            notes=notes,
            status=order.status,
            reviewed_at=order.reviewed_at,
            reviewed_by_id=order.reviewed_by_id,
        )
        # `auto_now_add` ignores a value passed to create().
        Refuelling.objects.filter(pk=entry.pk).update(created_at=order.created_at)
        # Detached first, or deleting the job would cascade to them.
        order.machine_refuels.update(refuelling=entry, work_order=None)
        if fuel_only:
            order.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0009_refuelling'),
    ]

    # One way only: deploy.sh refuses rollbacks, and the deleted jobs can't be rebuilt.
    operations = [
        migrations.RunPython(move_fill_ups),
    ]
