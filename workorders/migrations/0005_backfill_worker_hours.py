from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def backfill(apps, schema_editor):
    """Give jobs recorded before labour hours existed the hours the Hodiny tab
    used to show for them: every participant credited with the job's full
    machine hours. Without this, older jobs silently drop to zero there."""
    WorkOrder = apps.get_model('workorders', 'WorkOrder')
    WorkerHours = apps.get_model('workorders', 'WorkerHours')
    rows = []
    for work_order in WorkOrder.objects.prefetch_related('collaborators').annotate(
        machine_hours=Sum('machine_usages__hours')
    ):
        hours = work_order.machine_hours or Decimal('0')
        user_ids = {work_order.created_by_id} | {user.pk for user in work_order.collaborators.all()}
        rows.extend(WorkerHours(work_order=work_order, user_id=user_id, hours=hours) for user_id in user_ids)
    # Historical model, so there is no save() override to bypass — unlike
    # MachineUsage, WorkerHours keeps no denormalised counter in sync.
    WorkerHours.objects.bulk_create(rows)


class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0004_workerhours'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
