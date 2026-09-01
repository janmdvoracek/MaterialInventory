from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from inventory.models import StockMovement
from materials.models import Location, Machine, Material

from .models import MachineUsage, WorkerHours, WorkOrder


class TransformCreateTests(TestCase):
    def setUp(self):
        # Both materials are tonnes: the balance check sums the two sides
        # together, so a mixed-unit fixture would not mean anything. Matches the
        # real catalog, which is Zdroj/Frakce in `t` throughout.
        self.material_raw = Material.objects.create(sku='RAW', name='Štěrk', unit_of_measure='t')
        self.material_finished = Material.objects.create(sku='FIN', name='Frakce 8/16', unit_of_measure='t')
        self.location = Location.objects.create(name='Main Depot')
        self.worker = User.objects.create_user(username='worker', password='pw')
        self.other_worker = User.objects.create_user(username='other_worker', password='pw')
        self.machine_a = Machine.objects.create(name='Crusher A')
        self.machine_b = Machine.objects.create(name='Excavator B')

    def _post(
        self,
        consumed_rows,
        produced_rows,
        machine_rows=None,
        description='Test job',
        worker_rows=None,
        hours='1',
    ):
        machine_rows = machine_rows or []
        worker_rows = worker_rows or []
        data = {
            'description': description,
            'hours': hours,
            'consumed-TOTAL_FORMS': str(max(len(consumed_rows), 1)),
            'consumed-INITIAL_FORMS': '0',
            'consumed-MIN_NUM_FORMS': '0',
            'consumed-MAX_NUM_FORMS': '1000',
            'produced-TOTAL_FORMS': str(max(len(produced_rows), 1)),
            'produced-INITIAL_FORMS': '0',
            'produced-MIN_NUM_FORMS': '0',
            'produced-MAX_NUM_FORMS': '1000',
            'machines-TOTAL_FORMS': str(max(len(machine_rows), 1)),
            'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0',
            'machines-MAX_NUM_FORMS': '1000',
            'workers-TOTAL_FORMS': str(max(len(worker_rows), 1)),
            'workers-INITIAL_FORMS': '0',
            'workers-MIN_NUM_FORMS': '0',
            'workers-MAX_NUM_FORMS': '1000',
        }
        for i, row in enumerate(worker_rows):
            data[f'workers-{i}-user'] = row['user'].pk
            data[f'workers-{i}-hours'] = str(row['hours'])
        for i, row in enumerate(consumed_rows):
            data[f'consumed-{i}-material'] = row['material'].pk
            data[f'consumed-{i}-location'] = row['location'].pk
            data[f'consumed-{i}-quantity'] = str(row['quantity'])
        for i, row in enumerate(produced_rows):
            data[f'produced-{i}-material'] = row['material'].pk
            data[f'produced-{i}-location'] = row['location'].pk
            data[f'produced-{i}-quantity'] = str(row['quantity'])
        for i, row in enumerate(machine_rows):
            data[f'machines-{i}-machine'] = row['machine'].pk
            data[f'machines-{i}-hours'] = str(row['hours'])
            data[f'machines-{i}-tons'] = str(row.get('tons', '5'))
        self.client.force_login(self.worker)
        return self.client.post(reverse('transform_create'), data)

    def test_transform_create_requires_login(self):
        response = self.client.get(reverse('transform_create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_produce_only_is_rejected(self):
        # A job with no consumed side cannot balance, so it is refused outright
        # rather than compared against a zero total.
        response = self._post(
            [], [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}]
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_consume_only_is_rejected(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}], []
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_transform_consume_records_a_negative_line_item(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('5')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        consumed = StockMovement.objects.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.quantity, Decimal('-5'))

    def test_consume_is_not_limited_by_anything_recorded_before(self):
        # There are no stock balances any more: a job records what was actually
        # processed, however much that is, with nothing to check it against.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('9999')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('9999')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        consumed = StockMovement.objects.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.quantity, Decimal('-9999'))

    def test_repeated_material_rows_are_kept_as_separate_line_items(self):
        # The view used to combine these to stock-check them as one. With no
        # check left, each row is written as typed.
        response = self._post(
            [
                {'material': self.material_raw, 'location': self.location, 'quantity': Decimal('6')},
                {'material': self.material_raw, 'location': self.location, 'quantity': Decimal('6')},
            ],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('12')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        consumed = StockMovement.objects.filter(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.count(), 2)
        self.assertEqual(sum((m.quantity for m in consumed), Decimal('0')), Decimal('-12'))

    def test_transform_with_single_machine_increments_total_hours(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2.5'), 'tons': Decimal('8')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('2.5'))
        usage = MachineUsage.objects.get()
        self.assertEqual(usage.machine, self.machine_a)
        self.assertEqual(usage.hours, Decimal('2.5'))
        self.assertEqual(usage.work_order, WorkOrder.objects.get())

    def test_transform_records_tons_per_machine_row(self):
        # Tonnage is per machine and independent of the job's mass balance:
        # both machines put the same 3 t through, so these do not sum to the
        # consumed or produced total and are not checked against it.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('3')},
                {'machine': self.machine_b, 'hours': Decimal('1.5'), 'tons': Decimal('3')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineUsage.objects.get(machine=self.machine_a).tons, Decimal('3'))
        self.assertEqual(MachineUsage.objects.get(machine=self.machine_b).tons, Decimal('3'))

    def test_machine_row_missing_tons_rejected(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            'hours': '2',
            'consumed-TOTAL_FORMS': '1',
            'consumed-INITIAL_FORMS': '0',
            'consumed-MIN_NUM_FORMS': '0',
            'consumed-MAX_NUM_FORMS': '1000',
            # Balances the produced row below, so this POST is rejected for the
            # reason under test and not by the mass-balance check.
            'consumed-0-material': self.material_raw.pk,
            'consumed-0-location': self.location.pk,
            'consumed-0-quantity': '3',
            'produced-TOTAL_FORMS': '1',
            'produced-INITIAL_FORMS': '0',
            'produced-MIN_NUM_FORMS': '0',
            'produced-MAX_NUM_FORMS': '1000',
            'produced-0-material': self.material_finished.pk,
            'produced-0-location': self.location.pk,
            'produced-0-quantity': '3',
            'machines-TOTAL_FORMS': '1',
            'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0',
            'machines-MAX_NUM_FORMS': '1000',
            'machines-0-machine': self.machine_a.pk,
            'machines-0-hours': '2',
            'machines-0-tons': '',
            'workers-TOTAL_FORMS': '1',
            'workers-INITIAL_FORMS': '0',
            'workers-MIN_NUM_FORMS': '0',
            'workers-MAX_NUM_FORMS': '1000',
        }
        response = self.client.post(reverse('transform_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())
        # A rejected row must not have moved the counter either.
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('0'))

    def test_transform_with_chained_machines_each_increment_independently(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('8')},
                {'machine': self.machine_b, 'hours': Decimal('1.5'), 'tons': Decimal('8')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.machine_a.refresh_from_db()
        self.machine_b.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('2'))
        self.assertEqual(self.machine_b.total_hours, Decimal('1.5'))
        work_order = WorkOrder.objects.get()
        self.assertEqual(MachineUsage.objects.filter(work_order=work_order).count(), 2)

    def test_transform_same_machine_used_twice_accumulates(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('1')},
                {'machine': self.machine_a, 'hours': Decimal('1')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('2'))
        self.assertEqual(MachineUsage.objects.filter(machine=self.machine_a).count(), 2)

    def test_empty_machine_formset_is_optional(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertFalse(MachineUsage.objects.exists())

    def test_machine_row_missing_hours_rejected(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            # Own hours are required, so they must be valid here or the form
            # would fail for that reason instead of the one under test.
            'hours': '2',
            'consumed-TOTAL_FORMS': '1',
            'consumed-INITIAL_FORMS': '0',
            'consumed-MIN_NUM_FORMS': '0',
            'consumed-MAX_NUM_FORMS': '1000',
            # Balances the produced row below, so this POST is rejected for the
            # reason under test and not by the mass-balance check.
            'consumed-0-material': self.material_raw.pk,
            'consumed-0-location': self.location.pk,
            'consumed-0-quantity': '3',
            'produced-TOTAL_FORMS': '1',
            'produced-INITIAL_FORMS': '0',
            'produced-MIN_NUM_FORMS': '0',
            'produced-MAX_NUM_FORMS': '1000',
            'produced-0-material': self.material_finished.pk,
            'produced-0-location': self.location.pk,
            'produced-0-quantity': '3',
            'machines-TOTAL_FORMS': '1',
            'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0',
            'machines-MAX_NUM_FORMS': '1000',
            'machines-0-machine': self.machine_a.pk,
            'machines-0-hours': '',
            'machines-0-tons': '8',
            'workers-TOTAL_FORMS': '1',
            'workers-INITIAL_FORMS': '0',
            'workers-MIN_NUM_FORMS': '0',
            'workers-MAX_NUM_FORMS': '1000',
        }
        response = self.client.post(reverse('transform_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())

    def test_machine_row_missing_machine_rejected(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            # Own hours are required, so they must be valid here or the form
            # would fail for that reason instead of the one under test.
            'hours': '2',
            'consumed-TOTAL_FORMS': '1',
            'consumed-INITIAL_FORMS': '0',
            'consumed-MIN_NUM_FORMS': '0',
            'consumed-MAX_NUM_FORMS': '1000',
            # Balances the produced row below, so this POST is rejected for the
            # reason under test and not by the mass-balance check.
            'consumed-0-material': self.material_raw.pk,
            'consumed-0-location': self.location.pk,
            'consumed-0-quantity': '3',
            'produced-TOTAL_FORMS': '1',
            'produced-INITIAL_FORMS': '0',
            'produced-MIN_NUM_FORMS': '0',
            'produced-MAX_NUM_FORMS': '1000',
            'produced-0-material': self.material_finished.pk,
            'produced-0-location': self.location.pk,
            'produced-0-quantity': '3',
            'machines-TOTAL_FORMS': '1',
            'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0',
            'machines-MAX_NUM_FORMS': '1000',
            'machines-0-machine': '',
            'machines-0-hours': '2',
            'machines-0-tons': '8',
            'workers-TOTAL_FORMS': '1',
            'workers-INITIAL_FORMS': '0',
            'workers-MIN_NUM_FORMS': '0',
            'workers-MAX_NUM_FORMS': '1000',
        }
        response = self.client.post(reverse('transform_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())

    def test_transform_records_own_hours(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            hours='6.5',
        )
        self.assertRedirects(response, reverse('transform_create'))
        entry = WorkerHours.objects.get()
        self.assertEqual(entry.user, self.worker)
        self.assertEqual(entry.hours, Decimal('6.50'))
        self.assertEqual(entry.work_order, WorkOrder.objects.get())

    def test_transform_requires_own_hours(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            hours='',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_worker_row_records_that_persons_own_hours(self):
        # The collaborator's hours are their own, not a copy of the creator's.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            hours='6.5',
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('4')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkerHours.objects.get(user=self.worker).hours, Decimal('6.50'))
        self.assertEqual(WorkerHours.objects.get(user=self.other_worker).hours, Decimal('4.00'))

    def test_worker_row_makes_that_person_a_collaborator(self):
        # Naming someone in an hours row is the only way to collaborate now,
        # so this is what puts the job in their history.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('4')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        work_order = WorkOrder.objects.get()
        self.assertEqual(list(work_order.collaborators.all()), [self.other_worker])

    def test_transform_without_worker_rows_leaves_collaborators_empty(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        work_order = WorkOrder.objects.get()
        self.assertFalse(work_order.collaborators.exists())
        self.assertEqual(list(work_order.worker_hours.values_list('user', flat=True)), [self.worker.pk])

    def test_same_person_named_twice_has_their_hours_combined(self):
        # Same rule as the consumed rows — combine rather than fail, which the
        # unique constraint on (work_order, user) would otherwise do.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            worker_rows=[
                {'user': self.other_worker, 'hours': Decimal('3')},
                {'user': self.other_worker, 'hours': Decimal('1.5')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkerHours.objects.get(user=self.other_worker).hours, Decimal('4.50'))

    def test_worker_row_missing_hours_rejected(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            worker_rows=[{'user': self.other_worker, 'hours': ''}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_worker_row_missing_person_rejected(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            'hours': '2',
            'consumed-TOTAL_FORMS': '1',
            'consumed-INITIAL_FORMS': '0',
            'consumed-MIN_NUM_FORMS': '0',
            'consumed-MAX_NUM_FORMS': '1000',
            # Balances the produced row below, so this POST is rejected for the
            # reason under test and not by the mass-balance check.
            'consumed-0-material': self.material_raw.pk,
            'consumed-0-location': self.location.pk,
            'consumed-0-quantity': '3',
            'produced-TOTAL_FORMS': '1',
            'produced-INITIAL_FORMS': '0',
            'produced-MIN_NUM_FORMS': '0',
            'produced-MAX_NUM_FORMS': '1000',
            'produced-0-material': self.material_finished.pk,
            'produced-0-location': self.location.pk,
            'produced-0-quantity': '3',
            'machines-TOTAL_FORMS': '1',
            'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0',
            'machines-MAX_NUM_FORMS': '1000',
            'workers-TOTAL_FORMS': '1',
            'workers-INITIAL_FORMS': '0',
            'workers-MIN_NUM_FORMS': '0',
            'workers-MAX_NUM_FORMS': '1000',
            'workers-0-user': '',
            'workers-0-hours': '4',
        }
        response = self.client.post(reverse('transform_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_worker_field_excludes_self(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        choices = list(response.context['worker_formset'].forms[0].fields['user'].queryset)
        self.assertNotIn(self.worker, choices)
        self.assertIn(self.other_worker, choices)

    def test_worker_field_excludes_managers_and_admins_from_workers(self):
        manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        admin = User.objects.create_user(username='admin', password='pw', role=User.Role.ADMIN)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        choices = list(response.context['worker_formset'].forms[0].fields['user'].queryset)
        self.assertNotIn(manager, choices)
        self.assertNotIn(admin, choices)
        self.assertIn(self.other_worker, choices)

    def test_worker_field_includes_managers_and_admins_for_a_manager(self):
        manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        other_manager = User.objects.create_user(username='other_manager', password='pw', role=User.Role.MANAGER)
        self.client.force_login(manager)
        response = self.client.get(reverse('transform_create'))
        choices = list(response.context['worker_formset'].forms[0].fields['user'].queryset)
        self.assertIn(other_manager, choices)
        self.assertIn(self.worker, choices)

    def test_worker_cannot_log_hours_for_a_manager_via_post(self):
        # The queryset restriction has to hold against a hand-crafted POST, not
        # just a rendered dropdown.
        manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            worker_rows=[{'user': manager, 'hours': Decimal('4')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_one_submission_writes_line_items_hours_and_machine_usage_together(self):
        # The three record types are one job and share a transaction, even
        # though no stock check can fail inside it any more.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2')}],
            hours='6',
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('4')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        work_order = WorkOrder.objects.get()
        self.assertEqual(work_order.movements.count(), 2)
        self.assertEqual(work_order.machine_usages.count(), 1)
        self.assertEqual(work_order.worker_hours.count(), 2)
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('2'))

    def test_unbalanced_totals_are_rejected(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('10')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('9.5')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_unbalanced_job_writes_no_hours_or_machine_usage_either(self):
        # The balance check gates the whole submission, not just the material
        # rows — hours and machine runtime are part of the same job record.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('10')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('9.5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2')}],
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('4')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkerHours.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('0'))

    def test_balance_compares_totals_not_individual_rows(self):
        # The normal case: one input crushed into several output fractions.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('10')}],
            [
                {'material': self.material_finished, 'location': self.location, 'quantity': Decimal('4')},
                {'material': self.material_finished, 'location': self.location, 'quantity': Decimal('6')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        produced = StockMovement.objects.filter(movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE)
        self.assertEqual(produced.count(), 2)
        self.assertEqual(sum((m.quantity for m in produced), Decimal('0')), Decimal('10'))

    def test_balance_holds_across_many_rows_on_both_sides(self):
        response = self._post(
            [
                {'material': self.material_raw, 'location': self.location, 'quantity': Decimal('2.5')},
                {'material': self.material_raw, 'location': self.location, 'quantity': Decimal('7.5')},
            ],
            [
                {'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3.25')},
                {'material': self.material_finished, 'location': self.location, 'quantity': Decimal('6.75')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(StockMovement.objects.count(), 4)

    def test_trailing_zeros_do_not_break_the_balance(self):
        # Decimal compares numerically, so 5.00 and 5 are equal here.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('5.00')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('5')}],
        )
        self.assertRedirects(response, reverse('transform_create'))

    def test_smallest_recordable_difference_is_still_rejected(self):
        # The form accepts 2 decimal places, so this is the tightest mismatch it
        # can express. Exact equality means it must not slip through.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('5.01')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('5')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_unbalanced_error_reports_both_totals_with_a_czech_decimal_comma(self):
        # The totals are formatted in Python, so they need `localize` to match
        # the comma the templates print everywhere else.
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('10.25')}],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('9.5')}],
        )
        self.assertContains(response, 'spotřeba 10,25')
        self.assertContains(response, 'výroba 9,5')


class MachineUsageModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='worker', password='pw')
        self.work_order = WorkOrder.objects.create(created_by=self.user, description='job')
        self.machine_a = Machine.objects.create(name='A')
        self.machine_b = Machine.objects.create(name='B')

    def test_create_increments_total_hours(self):
        MachineUsage.objects.create(work_order=self.work_order, machine=self.machine_a, hours=Decimal('2'))
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('2'))

    def test_increasing_hours_adjusts_by_delta(self):
        usage = MachineUsage.objects.create(work_order=self.work_order, machine=self.machine_a, hours=Decimal('2'))
        usage.hours = Decimal('5')
        usage.save()
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('5'))

    def test_decreasing_hours_adjusts_by_delta(self):
        usage = MachineUsage.objects.create(work_order=self.work_order, machine=self.machine_a, hours=Decimal('5'))
        usage.hours = Decimal('2')
        usage.save()
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('2'))

    def test_reassigning_machine_moves_hours_between_machines(self):
        usage = MachineUsage.objects.create(work_order=self.work_order, machine=self.machine_a, hours=Decimal('4'))
        usage.machine = self.machine_b
        usage.save()
        self.machine_a.refresh_from_db()
        self.machine_b.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('0'))
        self.assertEqual(self.machine_b.total_hours, Decimal('4'))

    def test_delete_decrements_total_hours(self):
        usage = MachineUsage.objects.create(work_order=self.work_order, machine=self.machine_a, hours=Decimal('4'))
        usage.delete()
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('0'))


class WorkOrderAdminTests(TestCase):
    def setUp(self):
        self.material = Material.objects.create(sku='ADM1', name='Steel', unit_of_measure='kg')
        self.location = Location.objects.create(name='Depot')
        self.machine = Machine.objects.create(name='Warrior')
        self.admin_user = User.objects.create_superuser(username='admin', password='pw')

    def test_can_create_workorder_with_movement_and_machine_usage_via_admin(self):
        self.client.force_login(self.admin_user)
        data = {
            'description': 'Admin-created job',
            'movements-TOTAL_FORMS': '1',
            'movements-INITIAL_FORMS': '0',
            'movements-MIN_NUM_FORMS': '0',
            'movements-MAX_NUM_FORMS': '1000',
            'movements-0-material': self.material.pk,
            'movements-0-location': self.location.pk,
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
        }
        response = self.client.post(reverse('admin:workorders_workorder_add'), data)
        self.assertEqual(response.status_code, 302)

        work_order = WorkOrder.objects.get()
        self.assertEqual(work_order.description, 'Admin-created job')
        self.assertEqual(work_order.created_by, self.admin_user)

        movement = StockMovement.objects.get(work_order=work_order)
        self.assertEqual(movement.material, self.material)
        self.assertEqual(movement.location, self.location)
        self.assertEqual(movement.quantity, Decimal('25'))
        self.assertEqual(movement.created_by, self.admin_user)

        usage = MachineUsage.objects.get(work_order=work_order)
        self.assertEqual(usage.machine, self.machine)
        self.assertEqual(usage.hours, Decimal('3'))
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.total_hours, Decimal('3'))

        # Labour hours are correctable from the admin as well as the form.
        entry = WorkerHours.objects.get(work_order=work_order)
        self.assertEqual(entry.user, self.admin_user)
        self.assertEqual(entry.hours, Decimal('7'))


class MachineDashboardTests(TestCase):
    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.machine_active = Machine.objects.create(name='Crusher A', total_hours=Decimal('12.5'))
        self.machine_retired = Machine.objects.create(name='Old Excavator', total_hours=Decimal('99'), is_active=False)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_dashboard_lists_only_active_machines(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(list(response.context['machines']), [self.machine_active])

    def test_dashboard_shows_current_total_hours(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_dashboard'))
        # Comma decimal separator: template output is localised under cs.
        self.assertContains(response, '12,5 h')

    def test_dashboard_shows_both_rates(self):
        self.machine_active.hourly_rate = Decimal('83')
        self.machine_active.rate_per_ton = Decimal('35.50')
        self.machine_active.save()
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '83,00')
        self.assertContains(response, '35,50')

    def test_dashboard_shows_dash_for_unset_rates(self):
        # Both rates are optional, so an unpriced machine must still render a row.
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '—', count=2)


class MachineUsageHistoryTests(TestCase):
    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.other_machine = Machine.objects.create(name='Excavator B')

    def _usage(self, user, machine=None, hours=Decimal('1'), tons=None):
        work_order = WorkOrder.objects.create(created_by=user, description='job')
        return MachineUsage.objects.create(
            work_order=work_order, machine=machine or self.machine, hours=hours, tons=tons
        )

    def test_history_requires_login(self):
        response = self.client.get(reverse('machine_usage_history'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_worker_only_sees_own_usage(self):
        own = self._usage(self.worker)
        self._usage(self.manager)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_usage_history'))
        usages = response.context['page_obj'].object_list
        self.assertEqual(list(usages), [own])

    def test_worker_cannot_bypass_restriction_via_created_by_param(self):
        self._usage(self.worker)
        other = self._usage(self.manager)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_usage_history'), {'created_by': self.manager.pk})
        usages = response.context['page_obj'].object_list
        self.assertNotIn(other, usages)

    def test_worker_sees_usage_from_collaborated_work_order(self):
        work_order = WorkOrder.objects.create(created_by=self.manager, description='Joint job')
        work_order.collaborators.add(self.worker)
        shared = MachineUsage.objects.create(work_order=work_order, machine=self.machine, hours=Decimal('2'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_usage_history'))
        usages = response.context['page_obj'].object_list
        self.assertIn(shared, usages)

    def test_history_shows_tons(self):
        self._usage(self.worker, tons=Decimal('12.5'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_usage_history'))
        # Comma decimal separator: template output is localised under cs.
        self.assertContains(response, '12,50')

    def test_history_shows_dash_for_row_without_tons(self):
        # Rows written before the column existed have no tonnage; the table
        # must still render them.
        self._usage(self.worker)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_usage_history'))
        self.assertContains(response, '—')

    def test_created_by_filter_hidden_from_worker(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_usage_history'))
        self.assertNotIn('created_by', response.context['form'].fields)

    def test_created_by_filter_available_to_manager(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_usage_history'))
        self.assertIn('created_by', response.context['form'].fields)

    def test_manager_sees_usage_from_all_users(self):
        self._usage(self.worker)
        self._usage(self.manager)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_usage_history'))
        self.assertEqual(len(response.context['page_obj'].object_list), 2)

    def test_history_filters_by_machine(self):
        matching = self._usage(self.worker, machine=self.machine)
        self._usage(self.worker, machine=self.other_machine)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_usage_history'), {'machine': self.machine.pk})
        usages = response.context['page_obj'].object_list
        self.assertEqual(list(usages), [matching])

    def test_history_shows_no_rows_when_filter_is_invalid(self):
        self._usage(self.worker)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_usage_history'), {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(len(response.context['page_obj'].object_list), 0)


class TimeWorkedTests(TestCase):
    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.other_worker = User.objects.create_user(username='other', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.other_machine = Machine.objects.create(name='Screener B')

    def _job(self, creator, hours, collaborators=(), machine_hours=None, machine=None):
        """A job with `hours` of labour by `creator`, plus `(user, hours)` pairs
        for anyone who worked it alongside them. `machine_hours` is machine
        runtime, which is a separate figure and must not reach this tab."""
        work_order = WorkOrder.objects.create(created_by=creator, description='job')
        if hours is not None:
            WorkerHours.objects.create(work_order=work_order, user=creator, hours=hours)
        for user, user_hours in collaborators:
            work_order.collaborators.add(user)
            WorkerHours.objects.create(work_order=work_order, user=user, hours=user_hours)
        if machine_hours is not None:
            MachineUsage.objects.create(work_order=work_order, machine=machine or self.machine, hours=machine_hours)
        return work_order

    def _summary_for(self, response, user):
        for row in response.context['summary']:
            if row['user'] == user:
                return row
        return None

    def test_time_worked_requires_login(self):
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_worker_sees_own_total_hours(self):
        self._job(self.worker, Decimal('2.5'))
        self._job(self.worker, Decimal('1.5'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        row = self._summary_for(response, self.worker)
        self.assertEqual(row['hours'], Decimal('4.00'))
        self.assertEqual(row['orders'], 2)

    def test_worker_summary_excludes_other_people(self):
        self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('8'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual([row['user'] for row in response.context['summary']], [self.worker])

    def test_each_person_is_credited_their_own_typed_hours(self):
        self._job(self.worker, Decimal('3'), collaborators=[(self.other_worker, Decimal('2'))])
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(self._summary_for(response, self.worker)['hours'], Decimal('3.00'))
        self.assertEqual(self._summary_for(response, self.other_worker)['hours'], Decimal('2.00'))

    def test_machine_hours_do_not_reach_the_summary(self):
        # A 3-hour crushing run that took the operator 5 hours of labour: this
        # tab reports the 5, and nothing here is derived from machine runtime.
        self._job(self.worker, Decimal('5'), machine_hours=Decimal('3'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(self._summary_for(response, self.worker)['hours'], Decimal('5.00'))

    def test_collaborator_summary_does_not_leak_creator_total(self):
        # The job was created by someone else, so the creator must not show up
        # on the collaborating worker's screen.
        self._job(self.other_worker, Decimal('3'), collaborators=[(self.worker, Decimal('2'))])
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual([row['user'] for row in response.context['summary']], [self.worker])
        self.assertEqual(self._summary_for(response, self.worker)['hours'], Decimal('2.00'))

    def test_creator_hours_not_multiplied_by_collaborator_count(self):
        # Two collaborators make the participation filter's M2M join return the
        # job twice, so aggregating that queryset directly would credit the
        # creator with 6 hours for one 3-hour job (and list it twice below).
        # `time_worked` re-queries by pk to avoid it; drop that and this fails.
        third_worker = User.objects.create_user(username='third', password='pw', role=User.Role.WORKER)
        work_order = self._job(
            self.worker,
            Decimal('3'),
            collaborators=[(self.other_worker, Decimal('1')), (third_worker, Decimal('1'))],
        )
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        row = self._summary_for(response, self.worker)
        self.assertEqual(row['hours'], Decimal('3.00'))
        self.assertEqual(row['orders'], 1)
        listed = response.context['page_obj'].object_list
        self.assertEqual([o.pk for o in listed], [work_order.pk])
        self.assertEqual(listed[0].total_hours, Decimal('5.00'))

    def test_manager_filtering_to_one_worker_does_not_multiply_hours(self):
        # Same join, reached the other way: the `worker` filter is the manager's
        # route through _participation_filter.
        third_worker = User.objects.create_user(username='third', password='pw', role=User.Role.WORKER)
        work_order = self._job(
            self.worker,
            Decimal('3'),
            collaborators=[(self.other_worker, Decimal('1')), (third_worker, Decimal('1'))],
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'), {'worker': self.worker.pk})
        self.assertEqual(self._summary_for(response, self.worker)['hours'], Decimal('3.00'))
        self.assertEqual([o.pk for o in response.context['page_obj'].object_list], [work_order.pk])

    def test_manager_sees_every_worker(self):
        self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('5'))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'))
        users = {row['user'] for row in response.context['summary']}
        self.assertEqual(users, {self.worker, self.other_worker})

    def test_job_without_worker_hours_is_absent_from_the_summary(self):
        # Nothing on the Transform form can produce this — only a work order
        # created in the admin with no hours rows on it.
        work_order = self._job(self.worker, None)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(response.context['summary'], [])
        # It still belongs to the worker, so the job itself stays listed.
        self.assertEqual([o.pk for o in response.context['page_obj'].object_list], [work_order.pk])
        self.assertIsNone(response.context['page_obj'].object_list[0].total_hours)

    def test_worker_filter_hidden_from_worker(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertNotIn('worker', response.context['form'].fields)

    def test_worker_filter_available_to_manager(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'))
        self.assertIn('worker', response.context['form'].fields)

    def test_worker_cannot_bypass_restriction_via_worker_param(self):
        self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('8'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'), {'worker': self.other_worker.pk})
        self.assertNotIn(self.other_worker, [row['user'] for row in response.context['summary']])

    def test_manager_can_filter_to_one_worker(self):
        self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('8'))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'), {'worker': self.other_worker.pk})
        self.assertEqual([row['user'] for row in response.context['summary']], [self.other_worker])

    def test_filters_by_date_range(self):
        old = self._job(self.worker, Decimal('4'))
        WorkOrder.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=10))
        self._job(self.worker, Decimal('1'))
        self.client.force_login(self.worker)
        response = self.client.get(
            reverse('time_worked'), {'date_from': (date.today() - timedelta(days=1)).isoformat()}
        )
        self.assertEqual(self._summary_for(response, self.worker)['hours'], Decimal('1.00'))

    def test_shows_nothing_when_filter_is_invalid(self):
        self._job(self.worker, Decimal('4'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'), {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(response.context['summary'], [])
        self.assertEqual(len(response.context['page_obj'].object_list), 0)

    def test_detail_list_scoped_to_worker(self):
        own = self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('8'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual([o.pk for o in response.context['page_obj'].object_list], [own.pk])


class EmptyLabelTests(TestCase):
    """See inventory.tests.EmptyLabelTests — same guard for the workorder forms."""

    DJANGO_DEFAULT = '- Select an option -'

    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        Material.objects.create(sku='SKU1', name='Steel Bar', unit_of_measure='pcs')
        Location.objects.create(name='Main Depot')
        Machine.objects.create(name='Crusher A')

    def test_pages_have_no_english_placeholder(self):
        self.client.force_login(self.manager)
        for name in ('transform_create', 'machine_usage_history', 'time_worked'):
            with self.subTest(view=name):
                self.assertNotContains(self.client.get(reverse(name)), self.DJANGO_DEFAULT)

    def test_transform_rows_prompt_in_czech(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('transform_create'))
        self.assertContains(response, 'Materiál')
        self.assertContains(response, 'Lokalita')
        self.assertContains(response, 'Stroj')
        self.assertContains(response, 'Pracovník')

    def test_filter_forms_offer_all_in_czech(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('machine_usage_history')), 'Všechny stroje')
        self.assertContains(self.client.get(reverse('time_worked')), 'Všichni pracovníci')
