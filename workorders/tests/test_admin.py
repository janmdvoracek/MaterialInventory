"""The back office."""

import re
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from machines.models import FuelSource, Machine
from materials.models import Material
from workorders.models import MachineRefuel, MachineUsage, Refuelling, StockMovement, WorkerHours, WorkOrder

from .helpers import ReviewFixtureMixin, admin_job_payload, default_location


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

        # Labour hours are correctable from the admin as well as the form.
        entry = WorkerHours.objects.get(work_order=work_order)
        self.assertEqual(entry.user, self.admin_user)
        self.assertEqual(entry.hours, Decimal('7'))

    def test_can_create_a_refuelling_with_its_fill_ups_via_admin(self):
        source = FuelSource.objects.create(name='Nádrž dílna')
        self.client.force_login(self.admin_user)
        data = {
            'performed_on': timezone.localdate().isoformat(),
            'notes': '',
            'status': Refuelling.Status.APPROVED,
            'machine_refuels-TOTAL_FORMS': '1',
            'machine_refuels-INITIAL_FORMS': '0',
            'machine_refuels-MIN_NUM_FORMS': '0',
            'machine_refuels-MAX_NUM_FORMS': '1000',
            'machine_refuels-0-machine': self.machine.pk,
            'machine_refuels-0-source': source.pk,
            'machine_refuels-0-litres': '45',
        }
        response = self.client.post(reverse('admin:workorders_refuelling_add'), data)
        self.assertEqual(response.status_code, 302)
        refuelling = Refuelling.objects.get()
        self.assertEqual(refuelling.created_by, self.admin_user)
        refuel = MachineRefuel.objects.get(refuelling=refuelling)
        self.assertEqual((refuel.machine, refuel.source, refuel.litres), (self.machine, source, Decimal('45')))

    def test_fuel_sources_are_managed_like_locations(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('admin:machines_fuelsource_add'), {'name': 'Čerpací stanice', 'is_active': 'on'}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(FuelSource.objects.get(name='Čerpací stanice').is_active)


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


class AdminCollaboratorsTests(ReviewFixtureMixin, TestCase):
    """`collaborators` is derived from the hours rows in the admin too, never entered.

    A worker reaches Souhrn through `collaborators` (`participation_filter`),
    so an hours row added here without it left the job out of their view.
    """

    def setUp(self):
        super().setUp()
        self.job = self.submit_job(self.manager, worker_rows=[{'user': self.worker, 'hours': '2'}])
        self.client.force_login(User.objects.create_superuser(username='admin', password='pw'))

    def _save(self, **overrides):
        url = reverse('admin:workorders_workorder_change', args=[self.job.pk])
        response = self.client.post(url, admin_job_payload(self.job, **overrides))
        self.assertEqual(response.status_code, 302)

    def _stored_hours(self):
        return [{'id': row.pk, 'user': row.user_id, 'hours': row.hours} for row in self.job.worker_hours.all()]

    def test_an_hours_row_added_here_makes_a_collaborator(self):
        self._save(hours=[*self._stored_hours(), {'user': self.other_worker.pk, 'hours': '3'}])
        self.assertEqual(set(self.job.collaborators.all()), {self.worker, self.other_worker})
        # And so the job reaches that worker's Souhrn.
        self.client.force_login(self.other_worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual([job.pk for job in response.context['rows']], [self.job.pk])

    def test_a_removed_hours_row_drops_the_collaborator(self):
        rows = [{**row, 'DELETE': 'on'} if row['user'] == self.worker.pk else row for row in self._stored_hours()]
        self._save(hours=rows)
        self.assertFalse(self.job.collaborators.exists())

    def test_the_authors_own_hours_do_not_make_a_collaborator(self):
        self._save()
        self.assertEqual(list(self.job.collaborators.all()), [self.worker])

    def test_collaborators_cannot_be_entered_directly(self):
        url = reverse('admin:workorders_workorder_change', args=[self.job.pk])
        self.assertNotIn('collaborators', self.client.get(url).context['adminform'].form.fields)
        self._save(collaborators=[self.other_worker.pk])
        self.assertEqual(list(self.job.collaborators.all()), [self.worker])


class DeriveCollaboratorsMigrationTests(ReviewFixtureMixin, TestCase):
    """Migration 0012 rebuilds `collaborators` on jobs the admin let drift from their hours rows."""

    def test_existing_jobs_are_resynced(self):
        from importlib import import_module

        from django.apps import apps

        migration = import_module('workorders.migrations.0012_derive_collaborators')
        missing = self.submit_job(self.manager, worker_rows=[{'user': self.worker, 'hours': '2'}])
        missing.collaborators.clear()
        stray = self.submit_job(self.manager)
        stray.collaborators.add(self.other_worker)
        migration.derive_collaborators(apps, None)
        self.assertEqual(list(missing.collaborators.all()), [self.worker])
        self.assertFalse(stray.collaborators.exists())


class AdminNumberRulesTests(ReviewFixtureMixin, TestCase):
    """The admin inlines hold numbers to the entry forms' rules: decimal places and half-hour steps too,
    not just the limits. Otherwise a value saved here is refused when the entry is next opened on
    `job_edit` or `refuel_edit`."""

    def setUp(self):
        super().setUp()
        self.job = self.submit_job(
            self.manager,
            machine_rows=[{'machine': self.machine_a, 'hours': '100', 'tons': '5'}],
            worker_rows=[{'user': self.worker, 'hours': '2'}],
        )
        self.client.force_login(User.objects.create_superuser(username='admin', password='pw'))

    def _post(self, **overrides):
        url = reverse('admin:workorders_workorder_change', args=[self.job.pk])
        return self.client.post(url, admin_job_payload(self.job, **overrides))

    def test_stored_values_save_unchanged(self):
        # The admin re-posts 2.00 for a stored 2: trailing zeros are not decimal places.
        self.assertEqual(self._post().status_code, 302)

    def test_a_value_the_entry_form_refuses_is_refused_here(self):
        movement = self.job.movements.get(quantity__gt=0)
        usage = self.job.machine_usages.get()
        hours = self.job.worker_hours.get(user=self.worker)
        movements = [
            {'id': row.pk, 'material': row.material_id, 'movement_type': row.movement_type, 'quantity': row.quantity}
            for row in self.job.movements.all()
        ]
        usage_row = {'id': usage.pk, 'machine': usage.machine_id, 'hours': usage.hours, 'tons': usage.tons}
        hours_rows = [{'id': row.pk, 'user': row.user_id, 'hours': row.hours} for row in self.job.worker_hours.all()]
        cases = {
            'quantity with 3 decimals': {
                'movements': [{**row, 'quantity': '5.001'} if row['id'] == movement.pk else row for row in movements]
            },
            'meter reading off the half hour': {'usages': [{**usage_row, 'hours': '100.25'}]},
            'tonnes with 3 decimals': {'usages': [{**usage_row, 'tons': '5.001'}]},
            'worker hours off the half hour': {
                'hours': [{**row, 'hours': '2.25'} if row['id'] == hours.pk else row for row in hours_rows]
            },
            'worker hours below half an hour': {
                'hours': [{**row, 'hours': '0'} if row['id'] == hours.pk else row for row in hours_rows]
            },
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                self.assertEqual(self._post(**overrides).status_code, 200)
        movement.refresh_from_db()
        usage.refresh_from_db()
        hours.refresh_from_db()
        self.assertEqual((movement.quantity, usage.hours, usage.tons, hours.hours), (5, 100, 5, 2))

    def test_litres_with_3_decimals_are_refused(self):
        response = self.client.post(
            reverse('admin:workorders_refuelling_add'),
            {
                'performed_on': timezone.localdate().isoformat(),
                'notes': '',
                'status': Refuelling.Status.APPROVED,
                'machine_refuels-TOTAL_FORMS': '1',
                'machine_refuels-INITIAL_FORMS': '0',
                'machine_refuels-MIN_NUM_FORMS': '0',
                'machine_refuels-MAX_NUM_FORMS': '1000',
                'machine_refuels-0-machine': self.machine_a.pk,
                'machine_refuels-0-litres': '45.001',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Refuelling.objects.exists())

    def test_the_boxes_render_the_entry_forms_steps(self):
        html = self.client.get(reverse('admin:workorders_workorder_change', args=[self.job.pk])).content.decode()
        for name, step in (
            ('movements-0-quantity', '0.01'),
            ('machine_usages-0-hours', '0.5'),
            ('machine_usages-0-tons', '0.01'),
            ('worker_hours-0-hours', '0.5'),
        ):
            with self.subTest(name=name):
                tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)
                self.assertIn(f'step="{step}"', tag)


class AdminMovementSignTests(ReviewFixtureMixin, TestCase):
    """A line item's sign must match its type in the admin: consumed negative, produced positive, never zero.

    Materiál's net is a plain Sum of the signed quantities, and `job_edit` reads
    the typed amount back with `abs()`, so a wrong sign skews the one and is
    silently flipped by the other.
    """

    def setUp(self):
        super().setUp()
        self.job = self.submit_job(self.manager)
        self.client.force_login(User.objects.create_superuser(username='admin', password='pw'))

    def _post_with(self, movement_type, quantity):
        movements = [
            {'id': row.pk, 'material': row.material_id, 'movement_type': row.movement_type, 'quantity': row.quantity}
            for row in self.job.movements.all()
        ]
        movements = [
            {**row, 'quantity': quantity} if row['movement_type'] == movement_type else row for row in movements
        ]
        url = reverse('admin:workorders_workorder_change', args=[self.job.pk])
        return self.client.post(url, admin_job_payload(self.job, movements=movements))

    def test_a_wrong_or_zero_sign_is_refused(self):
        consume = StockMovement.MovementType.TRANSFORM_CONSUME
        produce = StockMovement.MovementType.TRANSFORM_PRODUCE
        for movement_type, quantity, message in (
            (consume, '5', 'Spotřeba se zapisuje záporným množstvím.'),
            (consume, '0', 'Spotřeba se zapisuje záporným množstvím.'),
            (produce, '-5', 'Výroba se zapisuje kladným množstvím.'),
            (produce, '0', 'Výroba se zapisuje kladným množstvím.'),
        ):
            with self.subTest(movement_type=movement_type, quantity=quantity):
                self.assertContains(self._post_with(movement_type, quantity), message)
        self.assertEqual(sorted(self.job.movements.values_list('quantity', flat=True)), [Decimal('-5'), Decimal('5')])

    def test_matching_signs_are_saved(self):
        response = self._post_with(StockMovement.MovementType.TRANSFORM_CONSUME, '-4.5')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.job.movements.get(quantity__lt=0).quantity, Decimal('-4.5'))
