"""Recording a job: the submission path and the rules it enforces."""

import re
from datetime import timedelta
from decimal import Decimal

from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from locations.models import Location
from machines.models import Machine
from materials.models import Material
from workorders.admin import WorkOrderAdminForm
from workorders.forms import (
    DESCRIPTION_MAX_LENGTH,
    NOTES_MAX_LENGTH,
)
from workorders.models import MachineUsage, StockMovement, WorkerHours, WorkOrder

from .helpers import ReviewFixtureMixin, default_location, job_payload


class TransformCreateTests(TestCase):
    def setUp(self):
        # Both materials are tonnes: the balance check sums the two sides
        # together, so a mixed-unit fixture would not mean anything. Matches the
        # real catalog, which is Zdroj/Frakce in `t` throughout.
        self.material_raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.material_finished = Material.objects.create(sku='FIN', name='Frakce 8/16')
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
            'notes': '',
            # Pre-filled on the real form, so every browser posts it.
            'location': default_location().pk,
            'performed_on': timezone.localdate().isoformat(),
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
            data[f'consumed-{i}-quantity'] = str(row['quantity'])
        for i, row in enumerate(produced_rows):
            data[f'produced-{i}-material'] = row['material'].pk
            data[f'produced-{i}-quantity'] = str(row['quantity'])
        for i, row in enumerate(machine_rows):
            data[f'machines-{i}-machine'] = row['machine'].pk
            if 'hours' in row:
                data[f'machines-{i}-hours'] = str(row['hours'])
                # Tuny go with the motohodiny.
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
        response = self._post([], [{'material': self.material_finished, 'quantity': Decimal('3')}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_consume_only_is_rejected(self):
        response = self._post([{'material': self.material_raw, 'quantity': Decimal('3')}], [])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_transform_consume_records_a_negative_line_item(self):
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        consumed = StockMovement.objects.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.quantity, Decimal('-5'))

    def test_consume_is_not_limited_by_anything_recorded_before(self):
        # There are no stock balances any more: a job records what was actually
        # processed, however much that is, with nothing to check it against.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('9999')}],
            [{'material': self.material_finished, 'quantity': Decimal('9999')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        consumed = StockMovement.objects.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.quantity, Decimal('-9999'))

    def test_the_same_material_twice_in_one_section_is_refused(self):
        # One material belongs on one row: two rows naming it are two halves of
        # a quantity that should have been typed once, and nothing downstream
        # can tell them apart afterwards. See DuplicateRowTests for the rule.
        response = self._post(
            [
                {'material': self.material_raw, 'quantity': Decimal('6')},
                {'material': self.material_raw, 'quantity': Decimal('6')},
            ],
            [{'material': self.material_finished, 'quantity': Decimal('12')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_transform_records_a_machine_row(self):
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2.5'), 'tons': Decimal('8')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        usage = MachineUsage.objects.get()
        self.assertEqual(usage.machine, self.machine_a)
        self.assertEqual(usage.hours, Decimal('2.5'))
        self.assertEqual(usage.work_order, WorkOrder.objects.get())

    def test_transform_records_tons_per_machine_row(self):
        # Tonnage is per machine and independent of the job's mass balance:
        # both machines put the same 3 t through, so these do not sum to the
        # consumed or produced total and are not checked against it.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('3')},
                {'machine': self.machine_b, 'hours': Decimal('1.5'), 'tons': Decimal('3')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineUsage.objects.get(machine=self.machine_a).tons, Decimal('3'))
        self.assertEqual(MachineUsage.objects.get(machine=self.machine_b).tons, Decimal('3'))

    def test_machine_row_missing_tons_accepted(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            'location': default_location().pk,
            'performed_on': timezone.localdate().isoformat(),
            'hours': '2',
            'consumed-TOTAL_FORMS': '1',
            'consumed-INITIAL_FORMS': '0',
            'consumed-MIN_NUM_FORMS': '0',
            'consumed-MAX_NUM_FORMS': '1000',
            # Balances the produced row below, so this POST is rejected for the
            # reason under test and not by the mass-balance check.
            'consumed-0-material': self.material_raw.pk,
            'consumed-0-quantity': '3',
            'produced-TOTAL_FORMS': '1',
            'produced-INITIAL_FORMS': '0',
            'produced-MIN_NUM_FORMS': '0',
            'produced-MAX_NUM_FORMS': '1000',
            'produced-0-material': self.material_finished.pk,
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
        # Optional while machines are priced outside the app: the blank number
        # is stored as unknown, not refused.
        self.assertRedirects(response, reverse('transform_create'))
        usage = MachineUsage.objects.get()
        self.assertEqual(usage.hours, Decimal('2.00'))
        self.assertIsNone(usage.tons)

    def test_transform_with_chained_machines_are_recorded_separately(self):
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('8')},
                {'machine': self.machine_b, 'hours': Decimal('1.5'), 'tons': Decimal('8')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        work_order = WorkOrder.objects.get()
        self.assertEqual(
            [(u.machine, u.hours) for u in MachineUsage.objects.filter(work_order=work_order).order_by('id')],
            [(self.machine_a, Decimal('2.00')), (self.machine_b, Decimal('1.50'))],
        )

    def test_transform_same_machine_used_twice_is_refused(self):
        # A machine that ran twice on one job ran for the sum of the two, on one
        # row. Two rows would double it in the Stroje totals with nothing to say
        # which is which.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('1')},
                {'machine': self.machine_a, 'hours': Decimal('1')},
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())

    def test_empty_machine_formset_is_optional(self):
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertFalse(MachineUsage.objects.exists())

    def test_machine_row_missing_hours_accepted(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            'location': default_location().pk,
            'performed_on': timezone.localdate().isoformat(),
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
            'consumed-0-quantity': '3',
            'produced-TOTAL_FORMS': '1',
            'produced-INITIAL_FORMS': '0',
            'produced-MIN_NUM_FORMS': '0',
            'produced-MAX_NUM_FORMS': '1000',
            'produced-0-material': self.material_finished.pk,
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
        # Optional while machines are priced outside the app: the blank number
        # is stored as unknown, not refused.
        self.assertRedirects(response, reverse('transform_create'))
        usage = MachineUsage.objects.get()
        self.assertIsNone(usage.hours)
        self.assertEqual(usage.tons, Decimal('8.00'))

    def test_machine_row_missing_machine_rejected(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            'location': default_location().pk,
            'performed_on': timezone.localdate().isoformat(),
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
            'consumed-0-quantity': '3',
            'produced-TOTAL_FORMS': '1',
            'produced-INITIAL_FORMS': '0',
            'produced-MIN_NUM_FORMS': '0',
            'produced-MAX_NUM_FORMS': '1000',
            'produced-0-material': self.material_finished.pk,
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
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            hours='6.5',
        )
        self.assertRedirects(response, reverse('transform_create'))
        entry = WorkerHours.objects.get()
        self.assertEqual(entry.user, self.worker)
        self.assertEqual(entry.hours, Decimal('6.50'))
        self.assertEqual(entry.work_order, WorkOrder.objects.get())

    def test_transform_requires_own_hours(self):
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            hours='',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_worker_row_records_that_persons_own_hours(self):
        # The collaborator's hours are their own, not a copy of the creator's.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
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
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('4')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        work_order = WorkOrder.objects.get()
        self.assertEqual(list(work_order.collaborators.all()), [self.other_worker])

    def test_transform_without_worker_rows_leaves_collaborators_empty(self):
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        work_order = WorkOrder.objects.get()
        self.assertFalse(work_order.collaborators.exists())
        self.assertEqual(list(work_order.worker_hours.values_list('user', flat=True)), [self.worker.pk])

    def test_same_person_named_twice_is_refused(self):
        # This used to sum the two rows, because the unique constraint on
        # (work_order, user) would otherwise have failed the write with a 500.
        # It is refused on the form now, where the person can see it.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            worker_rows=[
                {'user': self.other_worker, 'hours': Decimal('3')},
                {'user': self.other_worker, 'hours': Decimal('1.5')},
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_worker_row_missing_hours_rejected(self):
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            worker_rows=[{'user': self.other_worker, 'hours': ''}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_worker_row_missing_person_rejected(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            'location': default_location().pk,
            'performed_on': timezone.localdate().isoformat(),
            'hours': '2',
            'consumed-TOTAL_FORMS': '1',
            'consumed-INITIAL_FORMS': '0',
            'consumed-MIN_NUM_FORMS': '0',
            'consumed-MAX_NUM_FORMS': '1000',
            # Balances the produced row below, so this POST is rejected for the
            # reason under test and not by the mass-balance check.
            'consumed-0-material': self.material_raw.pk,
            'consumed-0-quantity': '3',
            'produced-TOTAL_FORMS': '1',
            'produced-INITIAL_FORMS': '0',
            'produced-MIN_NUM_FORMS': '0',
            'produced-MAX_NUM_FORMS': '1000',
            'produced-0-material': self.material_finished.pk,
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
            [{'material': self.material_raw, 'quantity': Decimal('3')}],
            [{'material': self.material_finished, 'quantity': Decimal('3')}],
            worker_rows=[{'user': manager, 'hours': Decimal('4')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_one_submission_writes_line_items_hours_and_machine_usage_together(self):
        # The three record types are one job and share a transaction, even
        # though no stock check can fail inside it any more.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2')}],
            hours='6',
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('4')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        work_order = WorkOrder.objects.get()
        self.assertEqual(work_order.movements.count(), 2)
        self.assertEqual(work_order.machine_usages.count(), 1)
        self.assertEqual(work_order.worker_hours.count(), 2)

    def test_unbalanced_totals_are_rejected(self):
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('10')}],
            [{'material': self.material_finished, 'quantity': Decimal('9.5')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_unbalanced_job_writes_no_hours_or_machine_usage_either(self):
        # The balance check gates the whole submission, not just the material
        # rows — hours and machine runtime are part of the same job record.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('10')}],
            [{'material': self.material_finished, 'quantity': Decimal('9.5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2')}],
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('4')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkerHours.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())

    def test_balance_compares_totals_not_individual_rows(self):
        # The normal case: one input crushed into several output fractions.
        # Different fractions, because a section refuses the same material twice.
        coarse = Material.objects.create(sku='FIN2', name='Frakce 16/32')
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('10')}],
            [
                {'material': self.material_finished, 'quantity': Decimal('4')},
                {'material': coarse, 'quantity': Decimal('6')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        produced = StockMovement.objects.filter(movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE)
        self.assertEqual(produced.count(), 2)
        self.assertEqual(sum((m.quantity for m in produced), Decimal('0')), Decimal('10'))

    def test_balance_holds_across_many_rows_on_both_sides(self):
        second_input = Material.objects.create(sku='RAW2', name='Výkopek')
        coarse = Material.objects.create(sku='FIN2', name='Frakce 16/32')
        response = self._post(
            [
                {'material': self.material_raw, 'quantity': Decimal('2.5')},
                {'material': second_input, 'quantity': Decimal('7.5')},
            ],
            [
                {'material': self.material_finished, 'quantity': Decimal('3.25')},
                {'material': coarse, 'quantity': Decimal('6.75')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(StockMovement.objects.count(), 4)

    def test_trailing_zeros_do_not_break_the_balance(self):
        # Decimal compares numerically, so 5.00 and 5 are equal here.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('5.00')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        self.assertRedirects(response, reverse('transform_create'))

    def test_smallest_recordable_difference_is_still_rejected(self):
        # The form accepts 2 decimal places, so this is the tightest mismatch it
        # can express. Exact equality means it must not slip through.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('5.01')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_unbalanced_error_reports_both_totals_with_a_czech_decimal_comma(self):
        # The totals are formatted in Python, so they need `localize` to match
        # the comma the templates print everywhere else.
        response = self._post(
            [{'material': self.material_raw, 'quantity': Decimal('10.25')}],
            [{'material': self.material_finished, 'quantity': Decimal('9.5')}],
        )
        self.assertContains(response, 'spotřeba 10,25')
        self.assertContains(response, 'výroba 9,5')


class PerformedOnTests(TestCase):
    """The day a job was done (`performed_on`), as opposed to when it was typed in."""

    def setUp(self):
        self.material_raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.material_finished = Material.objects.create(sku='FIN', name='Frakce 8/16')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.client.force_login(self.worker)

    def _submit(self, machine_rows=None, hours='1', **extra):
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=machine_rows,
            hours=hours,
        )
        payload.update(extra)
        return self.client.post(reverse('transform_create'), payload)

    def _approve(self, work_order):
        self.client.force_login(self.manager)
        self.client.post(reverse('job_approve', args=[work_order.pk]))

    def test_the_field_arrives_pre_filled_with_today(self):
        # A field-level `initial` callable, so it resolves through the bound
        # field rather than sitting in `form.initial`.
        response = self.client.get(reverse('transform_create'))
        self.assertEqual(response.context['order_form']['performed_on'].initial, timezone.localdate())

    def test_the_pre_filled_value_is_iso_so_the_widget_accepts_it(self):
        # `<input type="date">` only reads YYYY-MM-DD. Without an explicit
        # widget format the cs DATE_INPUT_FORMATS would render 02.09.2026, which
        # the browser rejects and shows as an empty box.
        response = self.client.get(reverse('transform_create'))
        self.assertContains(response, f'value="{timezone.localdate().isoformat()}"')

    def test_todays_date_is_stored(self):
        self._submit()
        self.assertEqual(WorkOrder.objects.get().performed_on, timezone.localdate())

    def test_an_edited_date_is_stored(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        self._submit(performed_on=yesterday.isoformat())
        self.assertEqual(WorkOrder.objects.get().performed_on, yesterday)

    def test_an_emptied_date_is_rejected(self):
        response = self._submit(performed_on='')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_a_future_date_is_rejected(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        response = self._submit(performed_on=tomorrow.isoformat())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'nemůže být v budoucnosti')

    def test_the_whole_job_is_refused_when_the_date_is_bad(self):
        # Same all-or-nothing rule as the mass balance: no hours, no machines,
        # no line items land while any part of the form is unusable.
        self._submit(performed_on='')
        self.assertFalse(WorkerHours.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_the_detail_page_shows_it(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        self._submit(performed_on=yesterday.isoformat())
        work_order = WorkOrder.objects.get()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_detail', args=[work_order.pk]))
        self.assertContains(response, yesterday.isoformat())
        self.assertContains(response, 'Datum provedení')

    def test_a_manager_can_correct_it(self):
        self._submit()
        work_order = WorkOrder.objects.get()
        corrected = timezone.localdate() - timedelta(days=3)
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        payload.update(performed_on=corrected.isoformat())
        self.client.force_login(self.manager)
        self.client.post(reverse('job_edit', args=[work_order.pk]), payload)
        work_order.refresh_from_db()
        self.assertEqual(work_order.performed_on, corrected)

    def test_the_edit_form_shows_the_jobs_own_date_not_today(self):
        # A correction that touches nothing else must not move the date, and the
        # box has to actually render it — see the ISO note above.
        yesterday = timezone.localdate() - timedelta(days=1)
        self._submit(performed_on=yesterday.isoformat())
        work_order = WorkOrder.objects.get()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[work_order.pk]))
        self.assertEqual(response.context['order_form'].initial['performed_on'], yesterday)
        self.assertContains(response, f'value="{yesterday.isoformat()}"')


class RecordingForSomeoneElseTests(TestCase):
    """A manager can record a job for a worker: it becomes the worker's job, while approval follows the manager."""

    def setUp(self):
        self.material_raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.material_finished = Material.objects.create(sku='FIN', name='Frakce 8/16')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.other_worker = User.objects.create_user(username='other', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)

    def _submit(self, as_user, **extra):
        self.client.force_login(as_user)
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            hours='6',
        )
        payload.update(extra)
        return self.client.post(reverse('transform_create'), payload)

    def test_the_field_is_offered_to_a_manager(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('transform_create'))
        self.assertIn('author', response.context['order_form'].fields)

    def test_a_worker_is_not_offered_the_field(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        self.assertNotIn('author', response.context['order_form'].fields)

    def test_a_worker_cannot_record_for_somebody_else_via_the_post(self):
        # The field is not on their form at all, so it is never cleaned.
        self._submit(self.worker, author=self.other_worker.pk)
        self.assertEqual(WorkOrder.objects.get().created_by, self.worker)

    def test_the_job_belongs_to_the_person_it_was_recorded_for(self):
        self._submit(self.manager, author=self.worker.pk)
        work_order = WorkOrder.objects.get()
        self.assertEqual(work_order.created_by, self.worker)

    def test_the_hours_are_the_workers(self):
        self._submit(self.manager, author=self.worker.pk)
        hours = WorkerHours.objects.get()
        self.assertEqual(hours.user, self.worker)
        self.assertEqual(hours.hours, Decimal('6.00'))

    def test_the_line_items_are_written_as_the_worker(self):
        self._submit(self.manager, author=self.worker.pk)
        self.assertEqual({m.created_by for m in StockMovement.objects.all()}, {self.worker})

    def test_it_is_approved_on_the_spot_with_the_manager_as_reviewer(self):
        # Approval follows the person at the keyboard, not the person the job
        # is for: a manager just saw the work written down.
        self._submit(self.manager, author=self.worker.pk)
        work_order = WorkOrder.objects.get()
        self.assertEqual(work_order.status, WorkOrder.Status.APPROVED)
        self.assertEqual(work_order.reviewed_by, self.manager)
        self.assertEqual(work_order.created_by, self.worker)

    def test_leaving_it_blank_records_for_the_manager(self):
        self._submit(self.manager)
        self.assertEqual(WorkOrder.objects.get().created_by, self.manager)

    def test_the_author_drops_out_of_the_collaborator_choices(self):
        # You cannot collaborate with yourself, and here "yourself" is the
        # person the job is being recorded for, not the manager typing it.
        self._submit(
            self.manager,
            author=self.worker.pk,
            **{'workers-0-user': str(self.other_worker.pk), 'workers-0-hours': '2'},
        )
        work_order = WorkOrder.objects.get()
        self.assertEqual(set(work_order.collaborators.all()), {self.other_worker})
        self.assertEqual(work_order.worker_hours.get(user=self.other_worker).hours, Decimal('2.00'))

    def test_a_manager_can_put_themselves_on_a_workers_job(self):
        # The workers-only rule is there to stop a worker assigning hours to a
        # manager; it does not apply to the manager doing the recording, who may
        # well have worked the job.
        self._submit(
            self.manager,
            author=self.worker.pk,
            **{'workers-0-user': str(self.manager.pk), 'workers-0-hours': '2'},
        )
        work_order = WorkOrder.objects.get()
        self.assertEqual(set(work_order.collaborators.all()), {self.manager})

    def test_a_worker_still_cannot_name_a_manager(self):
        response = self._submit(
            self.worker,
            **{'workers-0-user': str(self.manager.pk), 'workers-0-hours': '2'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_naming_the_author_as_their_own_collaborator_is_refused(self):
        response = self._submit(
            self.manager,
            author=self.worker.pk,
            **{'workers-0-user': str(self.worker.pk), 'workers-0-hours': '2'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_the_worker_sees_it_among_their_own_submissions(self):
        self._submit(self.manager, author=self.worker.pk)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [WorkOrder.objects.get()])


class DescriptionAndNotesTests(ReviewFixtureMixin, TestCase):
    """„Popis" is required; „Poznámky" is optional."""

    def _submit(self, url=None, **overrides):
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        payload.update(overrides)
        return self.client.post(url or reverse('transform_create'), payload)

    def test_a_job_without_a_description_is_refused(self):
        self.client.force_login(self.worker)
        response = self._submit(description='')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        # Nothing is written, so the complaint has to be on the page.
        self.assertContains(response, 'Toto pole je třeba vyplnit.')

    def test_a_description_at_the_cap_is_stored(self):
        description = 'x' * DESCRIPTION_MAX_LENGTH
        self.client.force_login(self.worker)
        self.assertEqual(self._submit(description=description).status_code, 302)
        self.assertEqual(WorkOrder.objects.get().description, description)

    def test_a_description_past_the_cap_is_refused(self):
        self.client.force_login(self.worker)
        response = self._submit(description='x' * (DESCRIPTION_MAX_LENGTH + 1))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertTrue(response.context['order_form'].has_error('description', code='max_length'))

    def test_the_description_input_carries_the_cap(self):
        # So the browser stops typing at the cap rather than refusing it on submit.
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        self.assertContains(response, f'maxlength="{DESCRIPTION_MAX_LENGTH}"')

    def test_the_admin_form_repeats_the_cap(self):
        # A longer description written there would be refused on `job_edit`.
        form = WorkOrderAdminForm()
        self.assertEqual(form.fields['description'].widget.attrs['maxlength'], str(DESCRIPTION_MAX_LENGTH))
        form = WorkOrderAdminForm(data={'description': 'x' * (DESCRIPTION_MAX_LENGTH + 1)})
        self.assertTrue(form.has_error('description', code='max_length'))

    def test_notes_are_optional(self):
        work_order = self.submit_job(self.worker)
        self.assertEqual(work_order.notes, '')

    def test_notes_are_stored_and_shown_on_the_job(self):
        note = 'Drtič se zasekl,\npokračovalo se až odpoledne.'
        work_order = self.submit_job(self.worker, notes=note)
        self.assertEqual(work_order.notes, note)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_detail', args=[work_order.pk]))
        self.assertContains(response, 'Poznámky')
        self.assertContains(response, 'Drtič se zasekl')

    def test_notes_longer_than_a_description_are_accepted(self):
        # The point of the field: `description` is capped at DESCRIPTION_MAX_LENGTH, this is not.
        note = 'x' * 1000
        work_order = self.submit_job(self.worker, notes=note)
        self.assertEqual(work_order.notes, note)

    def test_notes_past_the_form_cap_are_refused(self):
        self.client.force_login(self.worker)
        response = self._submit(notes='x' * (NOTES_MAX_LENGTH + 1))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_edit_prefills_and_rewrites_the_notes(self):
        work_order = self.submit_job(self.worker, notes='Původní poznámka')
        self.client.force_login(self.manager)
        url = reverse('job_edit', args=[work_order.pk])
        response = self.client.get(url)
        self.assertEqual(response.context['order_form'].initial['notes'], 'Původní poznámka')
        self.assertRedirects(
            self._submit(url=url, notes='Opravená poznámka'), reverse('job_detail', args=[work_order.pk])
        )
        work_order.refresh_from_db()
        self.assertEqual(work_order.notes, 'Opravená poznámka')

    def test_both_pages_render_the_notes_box(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        for url in (reverse('transform_create'), reverse('job_edit', args=[work_order.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'name="notes"')
                self.assertContains(response, '<textarea')


class ValueLimitTests(ReviewFixtureMixin, TestCase):
    """Every job-form number box has an upper bound, rendered as `max` and enforced, keeping values inside the
    decimal(12, 2) columns. The admin inlines repeat them.
    """

    def _payload(self, quantity='5', **overrides):
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': quantity}],
            [{'material': self.material_finished, 'quantity': quantity}],
            machine_rows=[{'machine': self.machine_a, 'hours': '2', 'tons': '5'}],
            worker_rows=[{'user': self.other_worker, 'hours': '1'}],
        )
        payload.update(overrides)
        return payload

    def _submit(self, payload):
        self.client.force_login(self.worker)
        return self.client.post(reverse('transform_create'), payload)

    def test_every_number_box_renders_its_limit(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        html = response.content.decode()
        for name, limit in (
            ('hours', '24'),
            ('consumed-0-quantity', '99999'),
            ('produced-0-quantity', '99999'),
            ('machines-0-hours', '99999'),
            ('machines-0-tons', '99999'),
            ('workers-0-hours', '24'),
        ):
            with self.subTest(name=name):
                tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)
                self.assertIn(f'max="{limit}"', tag)
        # Litres are on the Tankování form (`RefuellingFormTests` enforces them).
        html = self.client.get(reverse('refuel_create')).content.decode()
        tag = re.search(r'<input[^>]*name="refuels-0-litres"[^>]*>', html).group(0)
        self.assertIn('max="2000"', tag)

    def test_a_value_over_its_limit_is_refused_with_nothing_written(self):
        over = {
            'hours': '24.5',
            'workers-0-hours': '24.5',
            'consumed-0-quantity': '99999.01',
            'machines-0-hours': '99999.5',
            'machines-0-tons': '99999.01',
        }
        for name, value in over.items():
            with self.subTest(name=name):
                response = self._submit(self._payload(**{name: value}))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'menší nebo rovna')
                self.assertFalse(WorkOrder.objects.exists())

    def test_every_limit_itself_is_accepted_and_stored(self):
        # The bound is inclusive: one step less and a legitimate value is refused.
        payload = self._payload(
            quantity='99999',
            hours='24',
            **{
                'workers-0-hours': '24',
                'machines-0-hours': '99999',
                'machines-0-tons': '99999',
            },
        )
        response = self._submit(payload)
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkerHours.objects.get(user=self.worker).hours, Decimal('24'))
        self.assertEqual(WorkerHours.objects.get(user=self.other_worker).hours, Decimal('24'))
        usage = MachineUsage.objects.get()
        self.assertEqual((usage.hours, usage.tons), (Decimal('99999'), Decimal('99999')))
        self.assertEqual(
            sorted(StockMovement.objects.values_list('quantity', flat=True)), [Decimal('-99999'), Decimal('99999')]
        )

    def test_the_admin_inlines_carry_the_same_limits(self):
        # Without them a value saved in the admin would be refused the next
        # time the job is opened on `job_edit`.
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a, 'hours': '2'}])
        admin_user = User.objects.create_superuser(username='root', password='pw', role=User.Role.ADMIN)
        self.client.force_login(admin_user)
        html = self.client.get(reverse('admin:workorders_workorder_change', args=[work_order.pk])).content.decode()
        for name, limits in (
            ('movements-0-quantity', ('max="99999"', 'min="-99999"')),
            ('machine_usages-0-hours', ('max="99999"',)),
            ('machine_usages-0-tons', ('max="99999"',)),
            ('worker_hours-0-hours', ('max="24"',)),
        ):
            with self.subTest(name=name):
                tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)
                for limit in limits:
                    self.assertIn(limit, tag)
        # And the refuelling's own inline.
        html = self.client.get(reverse('admin:workorders_refuelling_add')).content.decode()
        tag = re.search(r'<input[^>]*name="machine_refuels-0-litres"[^>]*>', html).group(0)
        self.assertIn('max="2000"', tag)


class LocationTests(ReviewFixtureMixin, TestCase):
    """One location per job: a label, required, shown and filtered on Přehled and Materiál."""

    def setUp(self):
        super().setUp()
        self.north = default_location()
        self.south = Location.objects.create(name='Lom Jih')

    def _payload(self, **overrides):
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        payload.update(overrides)
        return payload

    def _job_at(self, location, quantity=Decimal('5')):
        job = self.submit_job(self.manager, quantity=quantity)
        WorkOrder.objects.filter(pk=job.pk).update(location=location)
        return job

    def test_job_is_recorded_with_its_location(self):
        self.assertEqual(self.submit_job(self.worker).location, self.north)

    def test_work_job_without_location_is_refused(self):
        self.client.force_login(self.worker)
        response = self.client.post(reverse('transform_create'), self._payload(location=''))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertEqual(response.context['order_form'].errors['location'], ['Toto pole je třeba vyplnit.'])
        # Rendered, or the refusal would look like a button that did nothing.
        self.assertContains(response, 'Toto pole je třeba vyplnit.')

    def test_retired_location_is_not_offered_on_a_new_job(self):
        self.south.is_active = False
        self.south.save(update_fields=['is_active'])
        self.client.force_login(self.worker)
        field = self.client.get(reverse('transform_create')).context['order_form'].fields['location']
        self.assertNotIn(self.south, field.queryset)
        response = self.client.post(reverse('transform_create'), self._payload(location=self.south.pk))
        self.assertIn('location', response.context['order_form'].errors)
        self.assertFalse(WorkOrder.objects.exists())

    def test_row_button_keeps_the_chosen_location(self):
        self.client.force_login(self.worker)
        response = self.client.post(reverse('transform_create'), self._payload(location=self.south.pk, add_consumed=''))
        self.assertEqual(response.context['order_form']['location'].value(), str(self.south.pk))
        self.assertFalse(WorkOrder.objects.exists())

    def test_edit_prefills_and_changes_the_location(self):
        job = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[job.pk]))
        self.assertEqual(response.context['order_form']['location'].value(), self.north.pk)
        response = self.client.post(reverse('job_edit', args=[job.pk]), self._payload(location=self.south.pk))
        self.assertRedirects(response, reverse('job_detail', args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.location, self.south)
        # Editing does not approve, location or not.
        self.assertEqual(job.status, WorkOrder.Status.PENDING)

    def test_edit_keeps_a_retired_location_the_job_names(self):
        job = self.submit_job(self.worker)
        self.north.is_active = False
        self.north.save(update_fields=['is_active'])
        other_retired = Location.objects.create(name='Stará', is_active=False)
        self.client.force_login(self.manager)
        field = self.client.get(reverse('job_edit', args=[job.pk])).context['order_form'].fields['location']
        self.assertIn(self.north, field.queryset)
        # Only the job's own: the rest of the retired catalog stays hidden.
        self.assertNotIn(other_retired, field.queryset)
        response = self.client.post(reverse('job_edit', args=[job.pk]), self._payload(location=self.north.pk))
        self.assertRedirects(response, reverse('job_detail', args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.location, self.north)

    def test_job_detail_shows_the_location(self):
        job = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('job_detail', args=[job.pk])), 'Lom Sever')

    def test_job_dashboard_filters_and_shows_the_location(self):
        self._job_at(self.north)
        south_job = self._job_at(self.south)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'location': self.south.pk})
        self.assertEqual([job.pk for job in response.context['page_obj']], [south_job.pk])
        self.assertContains(response, '<td>Lom Jih</td>', html=True)

    def test_material_dashboard_filters_summary_and_rows_by_location(self):
        self._job_at(self.north, quantity=Decimal('3'))
        self._job_at(self.south, quantity=Decimal('7'))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('material_dashboard'), {'location': self.south.pk})
        raw = next(material for material in response.context['materials'] if material == self.material_raw)
        self.assertEqual(raw.filtered_consumed, Decimal('7'))
        self.assertEqual({movement.work_order.location for movement in response.context['page_obj']}, {self.south})
        self.assertContains(response, '<td>Lom Jih</td>', html=True)

    def test_a_used_location_cannot_be_deleted(self):
        # Retired through `is_active` instead, like a machine or a material.
        self.submit_job(self.worker)
        with self.assertRaises(ProtectedError):
            self.north.delete()

    def test_admin_manages_the_location_catalog(self):
        admin = User.objects.create_superuser(username='root', password='pw', role=User.Role.ADMIN)
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse('admin:index')), 'Lokace')
        response = self.client.post(reverse('admin:locations_location_add'), {'name': 'Pískovna', 'is_active': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Location.objects.filter(name='Pískovna', is_active=True).exists())
