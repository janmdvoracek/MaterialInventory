"""The back office."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from machines.models import Machine
from materials.models import Material
from workorders.models import MachineRefuel, MachineUsage, StockMovement, WorkerHours, WorkOrder

from .helpers import ReviewFixtureMixin, default_location


class WorkOrderAdminTests(TestCase):
    def setUp(self):
        self.material = Material.objects.create(sku='ADM1', name='Steel')
        self.machine = Machine.objects.create(name='Warrior')
        self.admin_user = User.objects.create_superuser(username='admin', password='pw')

    def test_can_create_workorder_with_movement_and_machine_usage_via_admin(self):
        self.client.force_login(self.admin_user)
        data = {
            'description': 'Admin-created job',
            'location': default_location().pk,
            'performed_on': timezone.localdate().isoformat(),
            'status': WorkOrder.Status.APPROVED,
            'movements-TOTAL_FORMS': '1',
            'movements-INITIAL_FORMS': '0',
            'movements-MIN_NUM_FORMS': '0',
            'movements-MAX_NUM_FORMS': '1000',
            'movements-0-material': self.material.pk,
            'movements-0-movement_type': StockMovement.MovementType.TRANSFORM_PRODUCE,
            'movements-0-quantity': '25',
            'movements-0-notes': '',
            'machine_usages-TOTAL_FORMS': '1',
            'machine_usages-INITIAL_FORMS': '0',
            'machine_usages-MIN_NUM_FORMS': '0',
            'machine_usages-MAX_NUM_FORMS': '1000',
            'machine_usages-0-machine': self.machine.pk,
            'machine_usages-0-hours': '3',
            # Fuel is its own inline, so its own management form; a missing one
            # is a 200 with an invalid formset rather than a saved job.
            'machine_refuels-TOTAL_FORMS': '1',
            'machine_refuels-INITIAL_FORMS': '0',
            'machine_refuels-MIN_NUM_FORMS': '0',
            'machine_refuels-MAX_NUM_FORMS': '1000',
            'machine_refuels-0-machine': self.machine.pk,
            'machine_refuels-0-litres': '45',
            'worker_hours-TOTAL_FORMS': '1',
            'worker_hours-INITIAL_FORMS': '0',
            'worker_hours-MIN_NUM_FORMS': '0',
            'worker_hours-MAX_NUM_FORMS': '1000',
            'worker_hours-0-user': self.admin_user.pk,
            'worker_hours-0-hours': '7',
            'photos-TOTAL_FORMS': '0',
            'photos-INITIAL_FORMS': '0',
            'photos-MIN_NUM_FORMS': '0',
            'photos-MAX_NUM_FORMS': '10',
        }
        response = self.client.post(reverse('admin:workorders_workorder_add'), data)
        self.assertEqual(response.status_code, 302)

        work_order = WorkOrder.objects.get()
        self.assertEqual(work_order.description, 'Admin-created job')
        self.assertEqual(work_order.created_by, self.admin_user)

        movement = StockMovement.objects.get(work_order=work_order)
        self.assertEqual(movement.material, self.material)
        self.assertEqual(movement.quantity, Decimal('25'))
        self.assertEqual(movement.created_by, self.admin_user)

        usage = MachineUsage.objects.get(work_order=work_order)
        self.assertEqual(usage.machine, self.machine)
        self.assertEqual(usage.hours, Decimal('3'))

        refuel = MachineRefuel.objects.get(work_order=work_order)
        self.assertEqual(refuel.machine, self.machine)
        self.assertEqual(refuel.litres, Decimal('45'))

        # Labour hours are correctable from the admin as well as the form.
        entry = WorkerHours.objects.get(work_order=work_order)
        self.assertEqual(entry.user, self.admin_user)
        self.assertEqual(entry.hours, Decimal('7'))


class AdminJobDeleteTests(ReviewFixtureMixin, TestCase):
    """The admin deletes a job with all its rows, like `job_delete`, on both of its delete paths."""

    def setUp(self):
        super().setUp()
        self.work_order = self.submit_job(
            self.worker,
            machine_rows=[{'machine': self.machine_a, 'hours': '3'}],
            worker_rows=[{'user': self.other_worker, 'hours': '2'}],
        )
        self.admin_user = User.objects.create_superuser(username='admin', password='pw', role=User.Role.ADMIN)
        self.client.force_login(self.admin_user)

    def assertJobGone(self):
        self.assertFalse(WorkOrder.objects.filter(pk=self.work_order.pk).exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())

    def test_delete_page(self):
        url = reverse('admin:workorders_workorder_delete', args=[self.work_order.pk])
        self.assertNotContains(self.client.get(url), 'chráněn')
        response = self.client.post(url, {'post': 'yes'})
        self.assertRedirects(response, reverse('admin:workorders_workorder_changelist'))
        self.assertJobGone()

    def test_delete_selected_action(self):
        response = self.client.post(
            reverse('admin:workorders_workorder_changelist'),
            {'action': 'delete_selected', '_selected_action': [self.work_order.pk], 'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertJobGone()
