import csv
import io
import re
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.conf import settings
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from accounts.models import User
from locations.models import Location
from machines.models import Machine
from materials.models import Material

from .forms import JOB_SECTIONS, NOTES_MAX_LENGTH
from .models import MachineRefuel, MachineUsage, StockMovement, WorkerHours, WorkOrder
from .reports import FUEL_PAGE_PARAM, HISTORY_PAGE_SIZE, MY_JOBS_LIMIT, _last_month
from .views import MAX_ROWS_PER_SECTION, MIN_ROWS_PER_SECTION


def default_location():
    """The location every job payload names, since a work job cannot be recorded without one."""
    return Location.objects.get_or_create(name='Lom Sever')[0]


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
                # Tuny go with the motohodiny; a row naming only litres is a fill-up.
                data[f'machines-{i}-tons'] = str(row.get('tons', '5'))
            if 'litres' in row:
                data[f'machines-{i}-litres'] = str(row['litres'])
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

    def test_machine_row_missing_tons_rejected(self):
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
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())

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

    def test_machine_row_missing_hours_rejected(self):
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
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())

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


class PerformedOnTests(TestCase):
    """The date a job was actually done, as opposed to when it was typed in.

    The checkbox is what decides: unticked, the form fills in today whatever is
    sitting in the date box, so a stale value cannot silently back-date a job.
    """

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
    """A manager or admin can type a job in on a worker's behalf.

    The job is then the *worker's* — their name on it, their hours, their line
    items — while the manager stays the reviewer. Only the review status follows
    the person at the keyboard.
    """

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


class ReportsUsePerformedOnTests(TestCase):
    """Every report keys off the day the work happened, not the day it was typed.

    The two dates are deliberately far apart in these fixtures: a job entered
    today for work done 40 days ago must land in last month's figures and stay
    out of this week's, on all three pages.
    """

    def setUp(self):
        self.material_raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.material_finished = Material.objects.create(sku='FIN', name='Frakce 8/16')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.long_ago = timezone.localdate() - timedelta(days=40)

    def _back_dated_job(self):
        """A job recorded today for work done 40 days ago, approved."""
        self.client.force_login(self.manager)
        self.client.post(
            reverse('transform_create'),
            job_payload(
                [{'material': self.material_raw, 'quantity': Decimal('5')}],
                [{'material': self.material_finished, 'quantity': Decimal('5')}],
                machine_rows=[{'machine': self.machine, 'hours': Decimal('3'), 'tons': '7'}],
                hours='4',
            )
            | {'performed_on': self.long_ago.isoformat()},
        )
        work_order = WorkOrder.objects.get()
        # A manager's own submission is approved on the spot, so it is already
        # reportable; the point here is the date, not the review.
        self.assertTrue(work_order.is_approved)
        self.assertEqual(work_order.created_at.date(), timezone.localdate())
        return work_order

    def _last_week(self):
        return {'date_from': (timezone.localdate() - timedelta(days=6)).isoformat()}

    def _that_month(self):
        return {
            'date_from': (self.long_ago - timedelta(days=1)).isoformat(),
            'date_to': (self.long_ago + timedelta(days=1)).isoformat(),
        }

    def test_hours_land_in_the_period_the_work_was_done(self):
        self._back_dated_job()
        response = self.client.get(reverse('time_worked'), self._that_month())
        self.assertEqual(response.context['summary'][0]['hours'], Decimal('4.00'))

    def test_hours_stay_out_of_the_week_it_was_typed_in(self):
        self._back_dated_job()
        response = self.client.get(reverse('time_worked'), self._last_week())
        self.assertEqual(response.context['summary'], [])

    def test_machine_totals_follow_the_work(self):
        self._back_dated_job()
        response = self.client.get(reverse('machine_dashboard'), self._that_month())
        machine = response.context['machines'][0]
        self.assertEqual(machine.filtered_hours, Decimal('3.00'))
        self.assertEqual(machine.filtered_tons, Decimal('7.00'))

    def test_machine_totals_stay_out_of_the_week_it_was_typed_in(self):
        self._back_dated_job()
        response = self.client.get(reverse('machine_dashboard'), self._last_week())
        machine = response.context['machines'][0]
        self.assertIsNone(machine.filtered_hours)
        self.assertEqual(len(response.context['page_obj'].object_list), 0)

    def test_material_totals_follow_the_work(self):
        self._back_dated_job()
        response = self.client.get(reverse('material_dashboard'), self._that_month())
        produced = next(row for row in response.context['materials'] if row == self.material_finished)
        self.assertEqual(produced.filtered_produced, Decimal('5.000'))

    def test_material_totals_stay_out_of_the_week_it_was_typed_in(self):
        self._back_dated_job()
        response = self.client.get(reverse('material_dashboard'), self._last_week())
        produced = next(row for row in response.context['materials'] if row == self.material_finished)
        self.assertIsNone(produced.filtered_produced)
        self.assertEqual(len(response.context['page_obj'].object_list), 0)

    def test_the_line_item_is_dated_by_its_job(self):
        # A StockMovement has no timestamp of its own at all — its date is its
        # job's, which is the date the page has to show.
        self._back_dated_job()
        response = self.client.get(reverse('material_dashboard'))
        self.assertContains(response, self.long_ago.isoformat())

    def test_the_usage_row_is_dated_by_its_job(self):
        # MachineUsage.created_at is when the row was written, which job_edit
        # rewrites; the page shows the job's date instead.
        self._back_dated_job()
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, self.long_ago.isoformat())

    def test_the_review_dashboard_filters_and_dates_by_the_work(self):
        work_order = self._back_dated_job()
        self.assertEqual(
            list(self.client.get(reverse('job_dashboard'), self._that_month()).context['page_obj'].object_list),
            [work_order],
        )
        self.assertEqual(
            list(self.client.get(reverse('job_dashboard'), self._last_week()).context['page_obj'].object_list),
            [],
        )
        self.assertContains(self.client.get(reverse('job_dashboard')), self.long_ago.isoformat())

    def test_jobs_are_ordered_by_when_the_work_was_done(self):
        older = self._back_dated_job()
        self.client.post(
            reverse('transform_create'),
            job_payload(
                [{'material': self.material_raw, 'quantity': Decimal('5')}],
                [{'material': self.material_finished, 'quantity': Decimal('5')}],
            ),
        )
        newer = WorkOrder.objects.exclude(pk=older.pk).get()
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual(list(response.context['page_obj'].object_list), [newer, older])


class MachineDashboardTests(TestCase):
    def setUp(self):
        # The page is manager/admin-only, so a manager reads it — but the jobs
        # behind the numbers are still a worker's.
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine_active = Machine.objects.create(name='Crusher A')
        self.machine_retired = Machine.objects.create(name='Old Excavator', is_active=False)
        self.client.force_login(self.manager)

    def _usage(self, hours, tons=None, status=WorkOrder.Status.APPROVED):
        work_order = WorkOrder.objects.create(created_by=self.worker, description='job', status=status)
        return MachineUsage.objects.create(work_order=work_order, machine=self.machine_active, hours=hours, tons=tons)

    def test_dashboard_lists_only_active_machines(self):
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(list(response.context['machines']), [self.machine_active])

    def test_dashboard_lists_every_machine_when_nothing_is_filtered(self):
        # An idle machine is still part of the fleet; it reads 0 h rather than
        # dropping off the table.
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(list(response.context['machines']), [self.machine_active])
        self.assertContains(response, '0,0 h')

    def test_totals_follow_the_date_filter(self):
        # Back-dated on the job, not on the usage row: the row's own
        # created_at is when it was written, and the page dates it by its job.
        old = self._usage(Decimal('4'), tons=Decimal('10'))
        WorkOrder.objects.filter(pk=old.work_order_id).update(performed_on=date.today() - timedelta(days=40))
        self._usage(Decimal('1.5'), tons=Decimal('6'))
        response = self.client.get(
            reverse('machine_dashboard'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        machine = response.context['machines'][0]
        self.assertEqual(machine.filtered_hours, Decimal('1.50'))
        self.assertEqual(machine.filtered_tons, Decimal('6.00'))

    def test_naming_a_machine_narrows_the_table_to_it(self):
        other = Machine.objects.create(name='Excavator B')
        response = self.client.get(reverse('machine_dashboard'), {'machine': other.pk})
        self.assertEqual(list(response.context['machines']), [other])

    def test_an_invalid_filter_shows_no_machines(self):
        # A fleet of zeros under a "these are your filtered results" heading
        # would read as an answer; it is not one.
        self._usage(Decimal('3'))
        response = self.client.get(reverse('machine_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(list(response.context['machines']), [])

    def test_dashboard_shows_approved_hours(self):
        self._usage(Decimal('12.5'))
        response = self.client.get(reverse('machine_dashboard'))
        # Comma decimal separator: template output is localised under cs.
        self.assertContains(response, '12,5 h')

    def test_dashboard_shows_approved_tons(self):
        self._usage(Decimal('3'), tons=Decimal('12.5'))
        self._usage(Decimal('2'), tons=Decimal('8'))
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(response.context['machines'][0].filtered_tons, Decimal('20.50'))
        self.assertContains(response, '20,50')

    def test_dashboard_ignores_tons_from_unapproved_jobs(self):
        self._usage(Decimal('3'), tons=Decimal('12.5'), status=WorkOrder.Status.PENDING)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertIsNone(response.context['machines'][0].filtered_tons)

    def test_dashboard_shows_a_dash_when_no_tonnage_was_recorded(self):
        # `tons` is nullable because rows predating the column have no answer —
        # unknown, not zero. Sum skips them, and the page must not read that
        # back as a machine that processed nothing.
        self._usage(Decimal('3'))
        response = self.client.get(reverse('machine_dashboard'))
        self.assertIsNone(response.context['machines'][0].filtered_tons)
        self.assertContains(response, '3,0 h')

    def test_dashboard_ignores_hours_from_unapproved_jobs(self):
        # A job nobody has signed off must not move the number a manager reads
        # off this screen.
        self._usage(Decimal('4'), status=WorkOrder.Status.PENDING)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '0,0 h')

    def test_dashboard_shows_zero_for_machine_without_usage(self):
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '0,0 h')

    def test_dashboard_shows_both_rates(self):
        self.machine_active.hourly_rate = Decimal('83')
        self.machine_active.rate_per_ton = Decimal('35.50')
        self.machine_active.save()
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '83,00')
        self.assertContains(response, '35,50')

    def test_dashboard_shows_dash_for_unset_rates(self):
        # Both rates are optional, so an unpriced machine must still render a
        # row. Six dashes: an unused machine has no tonnage either, and the
        # three money columns are unknown rather than zero for a machine that
        # is not priced at all.
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '—', count=6)

    def test_cost_columns_multiply_the_totals_by_the_rates(self):
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.rate_per_ton = Decimal('35.50')
        self.machine_active.save()
        self._usage(Decimal('2.5'), tons=Decimal('10'))
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('200.00'))
        self.assertEqual(machine.filtered_tons_cost, Decimal('355.00'))
        self.assertEqual(machine.filtered_total_cost, Decimal('555.00'))

    def test_cost_columns_follow_the_filter(self):
        # The money is the same data at a third zoom level: it has to be the
        # cost of the rows the page is showing, not of the whole ledger.
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        old = self._usage(Decimal('4'))
        WorkOrder.objects.filter(pk=old.work_order_id).update(performed_on=date.today() - timedelta(days=40))
        self._usage(Decimal('1.5'))
        response = self.client.get(
            reverse('machine_dashboard'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        self.assertEqual(response.context['machines'][0].filtered_hours_cost, Decimal('120.00'))

    def test_unpriced_side_is_unknown_and_drops_out_of_the_total(self):
        # An unset rate means "not priced that way", not "free" — so that side
        # is a dash, and the total is what the machine *is* priced on.
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        self._usage(Decimal('2'), tons=Decimal('10'))
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('160.00'))
        self.assertIsNone(machine.filtered_tons_cost)
        self.assertEqual(machine.filtered_total_cost, Decimal('160.00'))

    def test_priced_machine_that_did_not_run_costs_zero_not_unknown(self):
        # The hours are a real zero, unlike a missing rate: a priced machine
        # that sat idle last week cost nothing, and that is an answer.
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('0'))
        self.assertEqual(machine.filtered_total_cost, Decimal('0'))

    def test_total_is_unknown_when_a_priced_side_is_unknown(self):
        # Billed per tonne, but the usage rows predate the `tons` column: the
        # real cost cannot be computed, and printing only the hours half under
        # „Celkem" would understate the bill.
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.rate_per_ton = Decimal('35.50')
        self.machine_active.save()
        self._usage(Decimal('2'), tons=None)
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('160.00'))
        self.assertIsNone(machine.filtered_tons_cost)
        self.assertIsNone(machine.filtered_total_cost)

    def test_costs_ignore_unapproved_jobs(self):
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        self._usage(Decimal('4'), status=WorkOrder.Status.PENDING)
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('0'))

    def test_costs_render_with_the_czech_comma(self):
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        self._usage(Decimal('2.5'))
        self.assertContains(self.client.get(reverse('machine_dashboard')), '200,00')


class MachineUsageDetailTests(TestCase):
    """The usage rows at the bottom of Stroje — worker scoping and filtering.

    Same page as the totals above them (`machine_dashboard`); these tests are
    about the rows.
    """

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.other_machine = Machine.objects.create(name='Excavator B')

    def _usage(self, user, machine=None, hours=Decimal('1'), tons=None, status=WorkOrder.Status.APPROVED):
        # Approved by default: the page reports signed-off jobs only, so an
        # unapproved fixture would be invisible for reasons unrelated to the
        # scoping these tests are about.
        work_order = WorkOrder.objects.create(created_by=user, description='job', status=status)
        return MachineUsage.objects.create(
            work_order=work_order, machine=machine or self.machine, hours=hours, tons=tons
        )

    def test_page_requires_login(self):
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_worker_gets_403(self):
        # Stroje is manager/admin-only in the view, not merely absent from a
        # worker's nav: typing the URL is a 403, not a scoped-down page.
        self._usage(self.worker)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_detail_shows_tons(self):
        self._usage(self.worker, tons=Decimal('12.5'))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        # Comma decimal separator: template output is localised under cs.
        self.assertContains(response, '12,50')

    def test_detail_shows_dash_for_row_without_tons(self):
        # Rows written before the column existed have no tonnage; the table
        # must still render them.
        self._usage(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '—')

    def test_created_by_filter_available_to_manager(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertIn('created_by', response.context['form'].fields)

    def test_manager_sees_usage_from_all_users(self):
        self._usage(self.worker)
        self._usage(self.manager)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(len(response.context['page_obj'].object_list), 2)

    def test_detail_filters_by_machine(self):
        matching = self._usage(self.worker, machine=self.machine)
        self._usage(self.worker, machine=self.other_machine)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'), {'machine': self.machine.pk})
        usages = response.context['page_obj'].object_list
        self.assertEqual(list(usages), [matching])

    def test_detail_shows_no_rows_when_filter_is_invalid(self):
        self._usage(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(len(response.context['page_obj'].object_list), 0)


class MaterialDashboardTests(TestCase):
    """Materiál: per-material tonnage and the line items behind it.

    The jobs are built directly rather than posted through the form, so a row
    can be back-dated or left unapproved without fighting the mass balance —
    which the form enforces and which is covered by its own tests.
    """

    def setUp(self):
        # Manager/admin-only page, so a manager reads it — but the jobs behind
        # the numbers are a worker's.
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.finished = Material.objects.create(sku='FIN', name='Frakce 8/16')
        self.retired = Material.objects.create(sku='OLD', name='Vyřazený písek', is_active=False)
        self.client.force_login(self.manager)

    def _job(self, consumed=None, produced=None, status=WorkOrder.Status.APPROVED, performed_on=None):
        work_order = WorkOrder.objects.create(created_by=self.worker, description='job', status=status)
        if performed_on is not None:
            WorkOrder.objects.filter(pk=work_order.pk).update(performed_on=performed_on)
        for material, quantity in consumed or []:
            StockMovement.objects.create(
                material=material,
                # Consumed quantities are stored negative; the page has to show
                # back the positive number the form asked for.
                quantity=-quantity,
                movement_type=StockMovement.MovementType.TRANSFORM_CONSUME,
                work_order=work_order,
                created_by=self.worker,
            )
        for material, quantity in produced or []:
            StockMovement.objects.create(
                material=material,
                quantity=quantity,
                movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE,
                work_order=work_order,
                created_by=self.worker,
            )
        return work_order

    def _row(self, response, material):
        return next(row for row in response.context['materials'] if row == material)

    def test_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_worker_gets_403(self):
        # Keeping the nav entry out of a worker's menu is not access control.
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse('material_dashboard')).status_code, 403)

    def test_produced_tonnage_totals_across_jobs(self):
        # The motivating question: how much 8/16 did we make?
        self._job(consumed=[(self.raw, Decimal('5'))], produced=[(self.finished, Decimal('5'))])
        self._job(consumed=[(self.raw, Decimal('3'))], produced=[(self.finished, Decimal('3'))])
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(self._row(response, self.finished).filtered_produced, Decimal('8.000'))
        self.assertContains(response, '8,00 t')

    def test_consumed_tonnage_reads_positive(self):
        self._job(consumed=[(self.raw, Decimal('12.5'))], produced=[(self.finished, Decimal('12.5'))])
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(self._row(response, self.raw).filtered_consumed, Decimal('12.500'))
        # Comma decimal separator: template output is localised under cs.
        self.assertContains(response, '12,50 t')

    def test_net_is_produced_minus_consumed(self):
        # Sand crushed out of gravel on one job and fed back into another: the
        # net is what the two directions leave behind.
        self._job(consumed=[(self.raw, Decimal('10'))], produced=[(self.finished, Decimal('10'))])
        self._job(consumed=[(self.finished, Decimal('4'))], produced=[(self.raw, Decimal('4'))])
        response = self.client.get(reverse('material_dashboard'))
        finished = self._row(response, self.finished)
        self.assertEqual(finished.filtered_produced, Decimal('10.000'))
        self.assertEqual(finished.filtered_consumed, Decimal('4.000'))
        self.assertEqual(finished.filtered_net, Decimal('6.000'))
        self.assertEqual(self._row(response, self.raw).filtered_net, Decimal('-6.000'))

    def test_unapproved_job_counts_for_nothing(self):
        # A pending job is a proposal, not evidence — invisible in every report.
        self._job(
            consumed=[(self.raw, Decimal('5'))],
            produced=[(self.finished, Decimal('5'))],
            status=WorkOrder.Status.PENDING,
        )
        response = self.client.get(reverse('material_dashboard'))
        self.assertIsNone(self._row(response, self.finished).filtered_produced)
        self.assertEqual(self._row(response, self.finished).filtered_net, Decimal('0'))
        self.assertEqual(len(response.context['page_obj'].object_list), 0)

    def test_material_with_no_movements_still_appears(self):
        # "We made none last month" is an answer; a missing row is not.
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(list(response.context['materials']), [self.finished, self.raw])
        self.assertContains(response, '0,00 t')

    def test_naming_a_material_narrows_the_table_to_it(self):
        response = self.client.get(reverse('material_dashboard'), {'material': self.finished.pk})
        self.assertEqual(list(response.context['materials']), [self.finished])

    def test_retired_material_is_out_of_the_summary_but_still_filterable(self):
        self._job(consumed=[(self.retired, Decimal('2'))], produced=[(self.finished, Decimal('2'))])
        response = self.client.get(reverse('material_dashboard'))
        self.assertNotIn(self.retired, list(response.context['materials']))
        # Its history is still readable — that is what the filter is for.
        self.assertIn(self.retired, response.context['form'].fields['material'].queryset)
        narrowed = self.client.get(reverse('material_dashboard'), {'material': self.retired.pk})
        self.assertEqual(len(narrowed.context['page_obj'].object_list), 1)

    def test_totals_follow_the_date_filter(self):
        self._job(
            consumed=[(self.raw, Decimal('4'))],
            produced=[(self.finished, Decimal('4'))],
            performed_on=date.today() - timedelta(days=40),
        )
        self._job(consumed=[(self.raw, Decimal('1.5'))], produced=[(self.finished, Decimal('1.5'))])
        response = self.client.get(
            reverse('material_dashboard'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        self.assertEqual(self._row(response, self.finished).filtered_produced, Decimal('1.500'))
        self.assertEqual(len(response.context['page_obj'].object_list), 2)

    def test_an_invalid_filter_shows_nothing(self):
        # A catalog of zeros under a "these are your filtered results" heading
        # would read as an answer; it is not one.
        self._job(consumed=[(self.raw, Decimal('5'))], produced=[(self.finished, Decimal('5'))])
        response = self.client.get(reverse('material_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(list(response.context['materials']), [])
        self.assertEqual(len(response.context['page_obj'].object_list), 0)

    def test_detail_rows_name_the_direction_and_the_author(self):
        self._job(consumed=[(self.raw, Decimal('5'))], produced=[(self.finished, Decimal('5'))])
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(len(response.context['page_obj'].object_list), 2)
        self.assertContains(response, 'Spotřeba')
        self.assertContains(response, 'Výroba')
        self.assertContains(response, self.worker.username)


class TimeWorkedTests(TestCase):
    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.other_worker = User.objects.create_user(username='other', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.other_machine = Machine.objects.create(name='Screener B')

    def _job(
        self, creator, hours, collaborators=(), machine_hours=None, machine=None, status=WorkOrder.Status.APPROVED
    ):
        """A job with `hours` of labour by `creator`, plus `(user, hours)` pairs
        for anyone who worked it alongside them. `machine_hours` is machine
        runtime, which is a separate figure and must not reach this tab.

        Approved by default: this tab reports signed-off jobs only."""
        work_order = WorkOrder.objects.create(created_by=creator, description='job', status=status)
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
        # route through participation_filter.
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
        WorkOrder.objects.filter(pk=old.pk).update(performed_on=date.today() - timedelta(days=10))
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


class TableExportTests(TestCase):
    """The „Stáhnout do CSV / Excelu" download under each exported table.

    Three summaries — „Souhrn" on Hodiny, „Stav strojů", „Souhrn materiálů" —
    and one detail table, Materiál's „Detail položek". Three things to hold:
    the file opens in Excel on a Czech machine (BOM, semicolons, comma
    decimals), it carries exactly the table the filter was showing, and it is
    gated like the page it hangs off — a worker must not be able to fetch a
    report the page itself would refuse them.
    """

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.other_worker = User.objects.create_user(username='other', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A', hourly_rate=Decimal('83'))
        self.raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.finished = Material.objects.create(sku='FIN', name='Frakce 8/16')

    def _job(
        self,
        *,
        performed_on=None,
        status=WorkOrder.Status.APPROVED,
        hours=None,
        usage=None,
        quantity=None,
        location=None,
    ):
        """One approved job carrying whichever of the three record types a test needs."""
        work_order = WorkOrder.objects.create(
            created_by=self.worker, description='job', status=status, location=location
        )
        if performed_on is not None:
            WorkOrder.objects.filter(pk=work_order.pk).update(performed_on=performed_on)
        for user, worked in hours or []:
            WorkerHours.objects.create(work_order=work_order, user=user, hours=worked)
        if usage is not None:
            machine_hours, tons = usage
            MachineUsage.objects.create(work_order=work_order, machine=self.machine, hours=machine_hours, tons=tons)
        if quantity is not None:
            StockMovement.objects.create(
                material=self.raw,
                quantity=-quantity,
                movement_type=StockMovement.MovementType.TRANSFORM_CONSUME,
                work_order=work_order,
                created_by=self.worker,
            )
            StockMovement.objects.create(
                material=self.finished,
                quantity=quantity,
                movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE,
                work_order=work_order,
                created_by=self.worker,
            )
        return work_order

    def _rows(self, response):
        """The downloaded file parsed the way Excel reads it — BOM dropped, split on `;`."""
        text = response.content.decode('utf-8-sig')
        return list(csv.reader(io.StringIO(text), delimiter=';'))

    def test_export_is_a_utf8_bom_csv_attachment(self):
        # The BOM is what makes Excel read the file as UTF-8 rather than showing
        # „Štěrk" as mojibake, and the date in the name keeps two exports of the
        # same report from colliding in one downloads folder.
        self.client.force_login(self.manager)
        response = self.client.get(reverse('material_dashboard_export'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="souhrn-materialu-{date.today().isoformat()}.csv"',
        )

    def test_numbers_use_the_czech_decimal_comma(self):
        # A dot would be read as a thousands separator (or as text) by an Excel
        # running under cs, so the file has to agree with what the page renders.
        self._job(usage=(Decimal('12.5'), Decimal('20.5')))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard_export'))
        self.assertIn('12,5;20,50;83,00', response.content.decode('utf-8-sig'))

    def test_machine_export_carries_the_summary_table(self):
        self._job(usage=(Decimal('12.5'), Decimal('20.5')))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_dashboard_export')))
        self.assertEqual(
            rows[0],
            [
                'Stroj',
                'Hodiny',
                'Tuny',
                'Sazba (Kč/hod)',
                'Cena (Kč/t)',
                'Cena za hodiny (Kč)',
                'Cena za tuny (Kč)',
                'Celkem (Kč)',
            ],
        )
        # The unset rate_per_ton is blank, not a dash: the page's „—" is text
        # that would break a column of numbers, and blank stays out of a SUM.
        # The machine is priced by the hour only, so „Celkem" is that side.
        # No thousands separator: `USE_THOUSAND_SEPARATOR` is off, and the
        # Czech one is a non-breaking space that Excel would not parse.
        self.assertEqual(rows[1], ['Crusher A', '12,5', '20,50', '83,00', '', '1037,50', '', '1037,50'])

    def test_unknown_cost_exports_blank_rather_than_zero(self):
        # Billed per tonne with the tonnage unknown: „Celkem" is not computable,
        # and a 0 Kč in a spreadsheet column would be summed as a free machine.
        Machine.objects.filter(pk=self.machine.pk).update(rate_per_ton=Decimal('35.50'))
        self._job(usage=(Decimal('2'), None))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_dashboard_export')))
        self.assertEqual(rows[1][5:], ['166,00', '', ''])

    def test_unknown_tonnage_exports_blank_but_idle_hours_export_zero(self):
        # `tons` is nullable because rows predating the column mean *unknown*,
        # while a machine with no rows in range really did run zero hours — the
        # same distinction the page draws with a dash and a 0.
        self._job(usage=(Decimal('3'), None))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_dashboard_export')))
        self.assertEqual(rows[1][1:3], ['3,0', ''])

    def test_material_export_carries_the_summary_table(self):
        self._job(quantity=Decimal('12.5'))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_dashboard_export')))
        self.assertEqual(rows[0], ['Materiál', 'Spotřebováno (t)', 'Vyrobeno (t)', 'Rozdíl (t)'])
        self.assertEqual(rows[1], ['Frakce 8/16', '0,00', '12,50', '12,50'])
        # Consumed reads positive, like the page: the form asked for a positive
        # number even though the row is stored negative.
        self.assertEqual(rows[2], ['Štěrk', '12,50', '0,00', '-12,50'])

    def test_hours_export_carries_the_summary_table(self):
        self._job(hours=[(self.worker, Decimal('2.5'))])
        self._job(hours=[(self.worker, Decimal('1.5'))])
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('time_worked_export')))
        self.assertEqual(rows[0], ['Pracovník', 'Hodiny', 'Zakázek'])
        self.assertEqual(rows[1], ['worker', '4,0', '2'])

    def test_export_follows_the_filter_on_the_page(self):
        # The link carries the page's querystring, so the file has to be the
        # table that was on screen and not the unfiltered report.
        self._job(usage=(Decimal('4'), Decimal('10')), performed_on=date.today() - timedelta(days=40))
        self._job(usage=(Decimal('1.5'), Decimal('6')))
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse('machine_dashboard_export'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        # The money follows the filter too — it is the same rows, priced.
        self.assertEqual(self._rows(response)[1], ['Crusher A', '1,5', '6,00', '83,00', '', '124,50', '', '124,50'])

    def test_unapproved_job_is_not_exported(self):
        # A pending job is invisible in every report; the download is one.
        self._job(usage=(Decimal('4'), Decimal('10')), status=WorkOrder.Status.PENDING)
        self.client.force_login(self.manager)
        self.assertEqual(self._rows(self.client.get(reverse('machine_dashboard_export')))[1][1:3], ['0,0', ''])

    def test_invalid_filter_exports_a_header_and_nothing_else(self):
        # Same rule as the page: an unusable filter must not fall through to
        # handing back the whole report under the heading of a filtered one.
        self._job(quantity=Decimal('5'))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_dashboard_export'), {'date_from': 'not-a-date'}))
        self.assertEqual(len(rows), 1)

    def test_hours_export_is_scoped_to_the_worker_downloading_it(self):
        # The one export a plain worker can reach, so it must be scoped exactly
        # like their Hodiny page — not the depot's hours.
        self._job(hours=[(self.worker, Decimal('2'))])
        self._job(hours=[(self.other_worker, Decimal('8'))])
        self.client.force_login(self.worker)
        rows = self._rows(self.client.get(reverse('time_worked_export')))
        self.assertEqual(rows[1:], [['worker', '2,0', '1']])

    def test_worker_cannot_reach_the_manager_only_exports(self):
        # Hiding the link is not access control; all three are gated like their
        # pages. The detail file is the most worth gating: it names every job's
        # author and description, not just totals.
        self.client.force_login(self.worker)
        for name in ('machine_dashboard_export', 'material_dashboard_export', 'material_detail_export'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_exports_require_login(self):
        for name in (
            'time_worked_export',
            'machine_dashboard_export',
            'material_dashboard_export',
            'material_detail_export',
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn('/login/', response.url)

    def test_detail_export_carries_the_line_items(self):
        # „Detail položek" one row per line item, in the page's own order and
        # with the same columns — the file is the table, not a rearrangement.
        work_order = self._job(quantity=Decimal('12.5'), location=default_location())
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_detail_export')))
        self.assertEqual(rows[0], ['Provedeno', 'Lokace', 'Materiál', 'Druh', 'Množství (t)', 'Zakázka', 'Kým'])
        today = date.today().isoformat()
        self.assertEqual(
            rows[1:],
            [
                [today, 'Lom Sever', 'Frakce 8/16', 'Výroba', '12,50', 'job', 'worker'],
                [today, 'Lom Sever', 'Štěrk', 'Spotřeba', '12,50', 'job', 'worker'],
            ],
        )
        self.assertEqual(work_order.movements.count(), 2)

    def test_detail_export_is_every_row_not_one_page(self):
        # The page paginates at HISTORY_PAGE_SIZE; a file holding the first 50
        # of 60 rows under the heading of the whole filter would be worse than
        # no file at all.
        for _ in range(HISTORY_PAGE_SIZE):
            self._job(quantity=Decimal('1'))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_detail_export')))
        self.assertEqual(len(rows) - 1, HISTORY_PAGE_SIZE * 2)

    def test_detail_export_follows_the_filter_and_hides_pending_jobs(self):
        # Same two rules as every other download, and for the same reason: it
        # is built from `_filtered_material_movements`, the page's own helper.
        self._job(quantity=Decimal('4'), performed_on=date.today() - timedelta(days=40))
        self._job(quantity=Decimal('9'), status=WorkOrder.Status.PENDING)
        self._job(quantity=Decimal('1.5'))
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse('material_detail_export'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        quantities = [row[4] for row in self._rows(response)[1:]]
        self.assertEqual(quantities, ['1,50', '1,50'])

    def test_detail_export_leaves_a_missing_description_blank(self):
        # The page prints „—" there; a dash is text in a column meant for text
        # the reader filters on, and blank is the same statement without it.
        WorkOrder.objects.filter(pk=self._job(quantity=Decimal('2')).pk).update(description='')
        self.client.force_login(self.manager)
        self.assertEqual(self._rows(self.client.get(reverse('material_detail_export')))[1][5], '')

    def test_detail_export_leaves_a_missing_location_blank(self):
        # Jobs recorded before the column existed, and fuel-only ones, have none.
        self._job(quantity=Decimal('2'))
        self.client.force_login(self.manager)
        self.assertEqual(self._rows(self.client.get(reverse('material_detail_export')))[1][1], '')

    def test_detail_export_is_a_bom_csv_named_for_the_day(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('material_detail_export'))
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="detail-polozek-{date.today().isoformat()}.csv"',
        )

    def test_invalid_filter_exports_a_header_and_nothing_else_on_the_detail_too(self):
        self._job(quantity=Decimal('5'))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_detail_export'), {'date_from': 'not-a-date'}))
        self.assertEqual(len(rows), 1)

    def test_both_material_downloads_are_linked_from_the_page(self):
        # One under each table, each carrying the filter that was on screen.
        self.client.force_login(self.manager)
        response = self.client.get(reverse('material_dashboard'), {'date_from': '2026-01-01'})
        for name in ('material_dashboard_export', 'material_detail_export'):
            with self.subTest(name=name):
                self.assertContains(response, f'{reverse(name)}?date_from=2026-01-01')


class EmptyLabelTests(TestCase):
    """Django 6 defaults a `ModelChoiceField`'s blank option to `- Select an
    option -`, which is not a translatable string and so renders English even
    under `cs`. Every such field therefore sets `empty_label` by hand, and this
    fails if a new one forgets.
    """

    DJANGO_DEFAULT = '- Select an option -'

    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        Material.objects.create(sku='SKU1', name='Steel Bar')
        Machine.objects.create(name='Crusher A')
        default_location()

    def test_pages_have_no_english_placeholder(self):
        self.client.force_login(self.manager)
        for name in ('transform_create', 'machine_dashboard', 'material_dashboard', 'time_worked', 'job_dashboard'):
            with self.subTest(view=name):
                self.assertNotContains(self.client.get(reverse(name)), self.DJANGO_DEFAULT)

    def test_transform_rows_prompt_in_czech(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('transform_create'))
        self.assertContains(response, 'Materiál')
        self.assertContains(response, 'Stroj')
        self.assertContains(response, 'Pracovník')
        # „Zapsat za" is a manager-only field, and this test is logged in as one.
        self.assertContains(response, 'Za sebe')
        self.assertContains(response, 'Vyberte lokaci')

    def test_filter_forms_offer_all_in_czech(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('machine_dashboard')), 'Všechny stroje')
        self.assertContains(self.client.get(reverse('material_dashboard')), 'Všechny materiály')
        self.assertContains(self.client.get(reverse('time_worked')), 'Všichni pracovníci')
        response = self.client.get(reverse('job_dashboard'))
        self.assertContains(response, 'Všichni uživatelé')
        self.assertContains(response, 'Všechny stavy')
        self.assertContains(response, 'Všechny lokace')
        self.assertContains(self.client.get(reverse('material_dashboard')), 'Všechny lokace')


def job_payload(
    consumed_rows,
    produced_rows,
    machine_rows=None,
    worker_rows=None,
    description='Test job',
    notes='',
    hours='1',
):
    """POST data for the Transform form, which the edit form re-uses verbatim."""
    machine_rows = machine_rows or []
    worker_rows = worker_rows or []
    data = {
        'description': description,
        'notes': notes,
        'hours': hours,
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
            # Tuny go with the motohodiny; a row naming only litres is a fill-up.
            data[f'machines-{i}-tons'] = str(row.get('tons', '5'))
        if 'litres' in row:
            data[f'machines-{i}-litres'] = str(row['litres'])
    return data


class ReviewFixtureMixin:
    """Catalog, people and a helper that records a job the way the app does."""

    def setUp(self):
        self.material_raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.material_finished = Material.objects.create(sku='FIN', name='Frakce 8/16')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.other_worker = User.objects.create_user(username='other', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine_a = Machine.objects.create(name='Crusher A')
        self.machine_b = Machine.objects.create(name='Excavator B')

    def submit_job(self, user, quantity=Decimal('5'), machine_rows=None, worker_rows=None, hours='1', notes=''):
        """Record a job through the real form, as `user` would."""
        self.client.force_login(user)
        response = self.client.post(
            reverse('transform_create'),
            job_payload(
                [{'material': self.material_raw, 'quantity': quantity}],
                [{'material': self.material_finished, 'quantity': quantity}],
                machine_rows=machine_rows,
                worker_rows=worker_rows,
                hours=hours,
                notes=notes,
            ),
        )
        self.assertRedirects(response, reverse('transform_create'))
        return WorkOrder.objects.order_by('-pk').first()


class JobReviewTests(ReviewFixtureMixin, TestCase):
    def test_worker_submission_waits_for_approval(self):
        work_order = self.submit_job(self.worker)
        self.assertEqual(work_order.status, WorkOrder.Status.PENDING)
        self.assertIsNone(work_order.reviewed_at)
        self.assertIsNone(work_order.reviewed_by)

    def test_manager_submission_is_approved_on_the_spot(self):
        # Nobody above a manager signs their work off, so waiting for approval
        # would leave it stuck forever.
        work_order = self.submit_job(self.manager)
        self.assertEqual(work_order.status, WorkOrder.Status.APPROVED)
        self.assertEqual(work_order.reviewed_by, self.manager)
        self.assertIsNotNone(work_order.reviewed_at)

    def test_superuser_submission_is_approved_on_the_spot(self):
        # createsuperuser leaves role=WORKER, and is_manager_or_admin covers it.
        admin_user = User.objects.create_superuser(username='root', password='pw')
        work_order = self.submit_job(admin_user)
        self.assertEqual(work_order.status, WorkOrder.Status.APPROVED)

    def test_worker_is_told_the_job_awaits_approval(self):
        # follow=True because the message is consumed by the page the redirect
        # lands on, which is where the worker actually reads it.
        self.client.force_login(self.worker)
        response = self.client.post(
            reverse('transform_create'),
            job_payload(
                [{'material': self.material_raw, 'quantity': Decimal('5')}],
                [{'material': self.material_finished, 'quantity': Decimal('5')}],
            ),
            follow=True,
        )
        self.assertContains(response, 'čeká na schválení')

    def test_pending_job_is_absent_from_time_worked(self):
        self.submit_job(self.worker, hours='3')
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(response.context['summary'], [])

    def test_pending_job_is_absent_from_machine_history(self):
        self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a, 'hours': '2'}])
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(len(response.context['page_obj'].object_list), 0)

    def test_approval_lets_the_job_into_the_reports(self):
        work_order = self.submit_job(self.worker, hours='3', machine_rows=[{'machine': self.machine_a, 'hours': '2'}])
        self.client.force_login(self.manager)
        self.client.post(reverse('job_approve', args=[work_order.pk]))

        response = self.client.get(reverse('time_worked'))
        self.assertEqual([row['user'] for row in response.context['summary']], [self.worker])
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(len(response.context['page_obj'].object_list), 1)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '2,0 h')

    def test_approve_records_who_signed_it_off(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_approve', args=[work_order.pk]))
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))
        work_order.refresh_from_db()
        self.assertEqual(work_order.status, WorkOrder.Status.APPROVED)
        self.assertEqual(work_order.reviewed_by, self.manager)
        self.assertIsNotNone(work_order.reviewed_at)

    def test_approve_rejects_a_get(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_approve', args=[work_order.pk]))
        self.assertEqual(response.status_code, 405)
        work_order.refresh_from_db()
        self.assertEqual(work_order.status, WorkOrder.Status.PENDING)

    def test_a_job_can_only_be_pending_or_approved(self):
        # Returning a job to its author is gone: a manager corrects it or
        # deletes it. Nothing else may end up in `status`.
        self.assertEqual([value for value, _ in WorkOrder.Status.choices], ['PENDING', 'APPROVED'])

    def test_there_is_no_return_url(self):
        with self.assertRaises(NoReverseMatch):
            reverse('job_return', args=[1])

    def test_the_detail_page_offers_only_approve_edit_and_delete(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_detail', args=[work_order.pk]))
        self.assertContains(response, 'Schválit')
        self.assertContains(response, 'Upravit')
        self.assertContains(response, 'Smazat')
        self.assertNotContains(response, 'Vrátit')

    def test_review_pages_are_closed_to_workers(self):
        # Hiding the nav entry is not access control: the views are gated too.
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.worker)
        for name, method in (
            ('job_dashboard', 'get'),
            ('job_detail', 'get'),
            ('job_edit', 'get'),
            ('job_delete', 'get'),
            ('job_approve', 'post'),
        ):
            with self.subTest(view=name):
                url = reverse(name) if name == 'job_dashboard' else reverse(name, args=[work_order.pk])
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 403)

    def test_review_pages_require_login(self):
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)


class MyJobsTests(ReviewFixtureMixin, TestCase):
    """The list of their own recent submissions at the top of Hodiny.

    A job is never handed back to its author, and a pending one is filtered out
    of every report, so this list is the only feedback a worker gets about what
    became of what they recorded — and Hodiny is the page the hours it is
    holding back are missing from.
    """

    def test_author_sees_their_own_pending_job(self):
        work_order = self.submit_job(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [work_order])
        self.assertContains(response, 'Čeká na schválení')

    def test_the_transform_page_no_longer_carries_the_list(self):
        self.submit_job(self.worker)
        response = self.client.get(reverse('transform_create'))
        self.assertNotIn('my_jobs', response.context)
        self.assertNotContains(response, 'Moje poslední zápisy')

    def test_a_manager_is_not_shown_the_list(self):
        # Hodiny is the whole depot's report for them, and Přehled already
        # lists every job with its status.
        self.submit_job(self.manager)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [])
        self.assertNotContains(response, 'Moje poslední zápisy')

    def test_the_status_follows_the_review(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        self.client.post(reverse('job_approve', args=[work_order.pk]))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertContains(response, 'Schváleno')

    def test_a_deleted_job_drops_off_the_list(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        self.client.post(reverse('job_delete', args=[work_order.pk]))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [])

    def test_someone_elses_job_is_not_listed(self):
        self.submit_job(self.worker)
        self.client.force_login(self.other_worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [])

    def test_a_job_someone_named_you_on_is_not_listed(self):
        # Submissions only — a collaborator did not record the job. Their hours
        # from it show up in the summary below once it is approved.
        self.submit_job(self.worker, worker_rows=[{'user': self.other_worker, 'hours': '2'}])
        self.client.force_login(self.other_worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [])

    def test_the_hours_shown_are_the_authors_own(self):
        # Not the job total: the collaborator's 2 h belong to them, not here.
        self.submit_job(self.worker, hours='3', worker_rows=[{'user': self.other_worker, 'hours': '2'}])
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(response.context['my_jobs'][0].my_hours, Decimal('3.00'))

    def test_the_list_ignores_the_hours_filters(self):
        # The filters scope the report below; a pending job is exactly what that
        # report cannot show, so the list must not be scoped away with it.
        work_order = self.submit_job(self.worker)
        response = self.client.get(reverse('time_worked'), {'date_from': '2000-01-01', 'date_to': '2000-01-02'})
        self.assertEqual(response.context['summary'], [])
        self.assertEqual(list(response.context['my_jobs']), [work_order])

    def test_only_the_most_recent_jobs_are_listed(self):
        for _ in range(MY_JOBS_LIMIT + 2):
            newest = self.submit_job(self.worker)
        response = self.client.get(reverse('time_worked'))
        my_jobs = list(response.context['my_jobs'])
        self.assertEqual(len(my_jobs), MY_JOBS_LIMIT)
        self.assertEqual(my_jobs[0], newest)


class DatePresetTests(ReviewFixtureMixin, TestCase):
    """The quick date ranges above every filter form.

    They are links, not a form field, so what they have to get right is the
    querystring: the dates themselves, everything else the user already picked,
    and dropping `page`.
    """

    LABELS = ('Vše', 'Posledních 7 dní', 'Posledních 30 dní', 'Minulý měsíc')
    PAGES = ('machine_dashboard', 'material_dashboard', 'time_worked', 'job_dashboard')

    def preset(self, response, key):
        return next(row for row in response.context['date_presets'] if row['key'] == key)

    def test_every_filtered_page_offers_the_same_ranges(self):
        # The manager sees all three; a worker only reaches two of them, and
        # those are covered by the scoping tests elsewhere.
        self.client.force_login(self.manager)
        for name in self.PAGES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual([row['label'] for row in response.context['date_presets']], list(self.LABELS))
                for label in self.LABELS:
                    self.assertContains(response, label)

    def test_the_ranges_end_today_and_include_it(self):
        today = timezone.localdate()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        for key, days in (('7d', 6), ('30d', 29)):
            with self.subTest(preset=key):
                querystring = self.preset(response, key)['querystring']
                self.assertIn(f'date_from={today - timedelta(days=days)}', querystring)
                self.assertIn(f'date_to={today}', querystring)

    def test_last_month_is_the_whole_previous_calendar_month(self):
        self.assertEqual(_last_month(date(2026, 3, 15)), (date(2026, 2, 1), date(2026, 2, 28)))
        # Across a year boundary, and onto a 31-day month.
        self.assertEqual(_last_month(date(2026, 1, 1)), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_a_range_actually_filters(self):
        recent = self.submit_job(self.worker)
        old = self.submit_job(self.worker)
        WorkOrder.objects.filter(pk=old.pk).update(performed_on=date.today() - timedelta(days=45))
        self.client.force_login(self.manager)
        querystring = self.preset(self.client.get(reverse('job_dashboard')), '30d')['querystring']
        response = self.client.get(f'{reverse("job_dashboard")}?{querystring}')
        self.assertEqual(list(response.context['page_obj'].object_list), [recent])

    def test_vse_clears_the_range(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'date_from': '2026-01-01', 'date_to': '2026-01-31'})
        self.assertEqual(self.preset(response, 'all')['querystring'], '')

    def test_the_range_in_effect_is_the_active_one(self):
        today = timezone.localdate()
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse('job_dashboard'),
            {'date_from': (today - timedelta(days=6)).isoformat(), 'date_to': today.isoformat()},
        )
        self.assertEqual([row['key'] for row in response.context['date_presets'] if row['active']], ['7d'])

    def test_nothing_is_active_until_a_range_is_picked(self):
        # "Vše" is: no dates at all, which is where an unfiltered page starts.
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual([row['key'] for row in response.context['date_presets'] if row['active']], ['all'])

    def test_the_other_filters_survive_the_tap(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'created_by': self.worker.pk, 'page': '2'})
        querystring = self.preset(response, '7d')['querystring']
        self.assertIn(f'created_by={self.worker.pk}', querystring)
        # A new range starts at page one.
        self.assertNotIn('page=', querystring)


class JobDashboardTests(ReviewFixtureMixin, TestCase):
    def test_dashboard_lists_every_job_whoever_recorded_it(self):
        mine = self.submit_job(self.manager)
        theirs = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual(set(response.context['page_obj'].object_list), {mine, theirs})

    def test_dashboard_highlights_jobs_awaiting_review(self):
        self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        self.assertContains(response, 'row-pending')
        self.assertEqual(response.context['pending_count'], 1)

    def test_dashboard_shows_the_hours_of_everyone_on_the_job(self):
        self.submit_job(self.worker, hours='3', worker_rows=[{'user': self.other_worker, 'hours': '2'}])
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual(response.context['page_obj'].object_list[0].total_hours, Decimal('5.00'))

    def test_dashboard_filters_by_status(self):
        approved = self.submit_job(self.manager)
        self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'status': WorkOrder.Status.APPROVED})
        self.assertEqual(list(response.context['page_obj'].object_list), [approved])

    def test_dashboard_filters_by_author(self):
        self.submit_job(self.manager)
        theirs = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'created_by': self.worker.pk})
        self.assertEqual(list(response.context['page_obj'].object_list), [theirs])

    def test_dashboard_shows_no_rows_when_filter_is_invalid(self):
        # An unusable filter must not fall through to showing everything.
        self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(len(response.context['page_obj'].object_list), 0)

    def test_detail_shows_everything_that_was_filled_in(self):
        work_order = self.submit_job(
            self.worker,
            quantity=Decimal('7.5'),
            hours='3',
            machine_rows=[{'machine': self.machine_a, 'hours': '2', 'tons': '4.25'}],
            worker_rows=[{'user': self.other_worker, 'hours': '1.5'}],
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_detail', args=[work_order.pk]))
        self.assertEqual(len(response.context['consumed']), 1)
        self.assertEqual(len(response.context['produced']), 1)
        # Consumed quantities are stored negative; the reviewer sees what was typed.
        self.assertEqual(response.context['consumed'][0].typed_quantity, Decimal('7.5'))
        self.assertEqual(len(response.context['machine_usages']), 1)
        self.assertEqual(len(response.context['worker_hours']), 2)
        self.assertContains(response, 'Crusher A')
        self.assertContains(response, '4,25')


class JobEditTests(ReviewFixtureMixin, TestCase):
    def _edit(self, work_order, **kwargs):
        self.client.force_login(self.manager)
        return self.client.post(reverse('job_edit', args=[work_order.pk]), job_payload(**kwargs))

    def test_edit_form_is_prefilled_with_what_was_recorded(self):
        work_order = self.submit_job(
            self.worker, quantity=Decimal('5'), hours='3', machine_rows=[{'machine': self.machine_a, 'hours': '2'}]
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[work_order.pk]))
        self.assertEqual(response.context['order_form'].initial['hours'], Decimal('3'))
        self.assertEqual(
            response.context['consumed_formset'].initial,
            [{'material': self.material_raw.pk, 'quantity': Decimal('5')}],
        )
        self.assertEqual(response.context['machine_formset'].initial[0]['hours'], Decimal('2'))

    def test_prefilled_form_can_be_saved_unchanged(self):
        # The models store more decimal places than the form accepts, so a raw
        # prefill would make the edit form reject its own values.
        work_order = self.submit_job(
            self.worker, quantity=Decimal('5'), hours='1.5', machine_rows=[{'machine': self.machine_a, 'hours': '2'}]
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[work_order.pk]))
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': '2'}],
            hours=str(response.context['order_form'].initial['hours']),
        )
        response = self.client.post(reverse('job_edit', args=[work_order.pk]), data)
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))

    def test_edit_rewrites_the_line_items(self):
        work_order = self.submit_job(self.worker, quantity=Decimal('5'))
        response = self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('8')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('8')}],
            description='Opraveno',
        )
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))
        work_order.refresh_from_db()
        self.assertEqual(work_order.description, 'Opraveno')
        self.assertEqual(work_order.movements.count(), 2)
        consumed = work_order.movements.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.quantity, Decimal('-8'))
        # The rows still belong to whoever did the work, not to the reviewer.
        self.assertEqual(consumed.created_by, self.worker)

    def test_edit_does_not_approve_the_job(self):
        work_order = self.submit_job(self.worker)
        self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        work_order.refresh_from_db()
        self.assertEqual(work_order.status, WorkOrder.Status.PENDING)

    def test_edit_rejects_unbalanced_totals(self):
        work_order = self.submit_job(self.worker, quantity=Decimal('5'))
        response = self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('8')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        self.assertEqual(response.status_code, 200)
        consumed = work_order.movements.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.quantity, Decimal('-5'))

    def test_edit_rewrites_the_hours_and_collaborators(self):
        work_order = self.submit_job(self.worker, hours='3', worker_rows=[{'user': self.other_worker, 'hours': '2'}])
        self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            hours='4',
        )
        self.assertEqual(
            {(row.user, row.hours) for row in work_order.worker_hours.all()},
            {(self.worker, Decimal('4.00'))},
        )
        # Collaborators stay derived from the hours rows.
        self.assertEqual(list(work_order.collaborators.all()), [])

    def test_edit_replaces_the_machine_rows(self):
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a, 'hours': '3'}])
        self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_b, 'hours': '5'}],
        )
        self.assertEqual(
            [(u.machine, u.hours) for u in work_order.machine_usages.all()],
            [(self.machine_b, Decimal('5.00'))],
        )

    def test_collaborator_choices_exclude_the_author_not_the_reviewer(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[work_order.pk]))
        choices = set(response.context['worker_formset'].forms[0].fields['user'].queryset)
        self.assertNotIn(self.worker, choices)
        self.assertIn(self.other_worker, choices)


class JobDeleteTests(ReviewFixtureMixin, TestCase):
    def test_confirmation_page_lists_what_will_go(self):
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a, 'hours': '3'}])
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_delete', args=[work_order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['machine_usages']), 1)
        self.assertTrue(WorkOrder.objects.filter(pk=work_order.pk).exists())

    def test_delete_removes_the_job_and_all_its_rows(self):
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a, 'hours': '3'}], hours='2')
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_delete', args=[work_order.pk]))
        self.assertRedirects(response, reverse('job_dashboard'))
        self.assertFalse(WorkOrder.objects.filter(pk=work_order.pk).exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())


class AdminJobDeleteTests(ReviewFixtureMixin, TestCase):
    """The admin deletes a job with everything on it, like `job_delete` does.

    The line items' FK used to be PROTECT, so the admin refused to delete any
    job that had materials — every real job — on both of its delete paths.
    """

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


class DuplicateRowTests(ReviewFixtureMixin, TestCase):
    """No section of the job form may name the same thing on two rows.

    Two rows naming one material are two halves of a quantity that should have
    been typed once; two rows naming one machine double it in the Stroje totals;
    two rows naming one person used to be silently added together, because the
    unique constraint on (work_order, user) would otherwise have failed the
    write. In every case the repeated row carries nothing the first one does
    not, and nobody reading the job back can tell what was meant.

    Two halves are tested here: the rule (`clean()`, on the POST) and the help
    (an unbound page does not offer what another row already took). The second
    is presentation, and server-side it can only be as fresh as the last
    render, which is exactly why the first exists. `job_rows.js` narrows the
    same lists live as the dropdowns change, but it is enhancement and the
    suite runs no browser, so everything below is the server's own behaviour —
    the half that has to be right whether or not the script loads.
    """

    def _submit(self, **kwargs):
        self.client.force_login(self.worker)
        return self.client.post(reverse('transform_create'), job_payload(**kwargs))

    # -------------------------------------------------------------- the rule

    def test_the_same_material_twice_on_the_consumed_side_is_refused(self):
        response = self._submit(
            consumed_rows=[
                {'material': self.material_raw, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('3')},
            ],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_the_same_material_twice_on_the_produced_side_is_refused(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[
                {'material': self.material_finished, 'quantity': Decimal('2')},
                {'material': self.material_finished, 'quantity': Decimal('3')},
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_the_complaint_names_the_material_and_is_czech(self):
        response = self._submit(
            consumed_rows=[
                {'material': self.material_raw, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('3')},
            ],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        self.assertContains(response, 'je v této sekci vybraný víckrát')
        self.assertContains(response, str(self.material_raw))

    def test_the_complaint_sits_on_the_repeated_row_not_the_first_one(self):
        """It names one row of several, so it has to render next to that row."""
        response = self._submit(
            consumed_rows=[
                {'material': self.material_raw, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('3')},
            ],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        formset = response.context['consumed_formset']
        self.assertEqual(formset.forms[0].non_field_errors(), [])
        self.assertEqual(len(formset.forms[1].non_field_errors()), 1)

    def test_one_material_may_still_be_consumed_and_produced_by_one_job(self):
        """The rule is per section: those are two statements about a material,
        not one typed twice."""
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(StockMovement.objects.filter(material=self.material_raw).count(), 2)

    def test_different_materials_on_one_side_are_still_fine(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[
                {'material': self.material_finished, 'quantity': Decimal('3')},
                {'material': self.material_raw, 'quantity': Decimal('2')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkOrder.objects.get().movements.count(), 3)

    def test_the_same_machine_twice_is_refused(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('1')},
                {'machine': self.machine_a, 'hours': Decimal('2')},
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'je vybraný víckrát')
        self.assertFalse(MachineUsage.objects.exists())

    def test_chained_machines_are_still_fine(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('1')},
                {'machine': self.machine_b, 'hours': Decimal('2')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineUsage.objects.count(), 2)

    def test_the_same_collaborator_twice_is_refused(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            worker_rows=[
                {'user': self.other_worker, 'hours': Decimal('3')},
                {'user': self.other_worker, 'hours': Decimal('1.5')},
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'je vybraný víckrát')
        self.assertFalse(WorkerHours.objects.exists())

    def test_a_duplicate_row_refuses_the_whole_job(self):
        """Same rule as the mass balance: nothing at all is written."""
        self._submit(
            consumed_rows=[
                {'material': self.material_raw, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('3')},
            ],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('1')}],
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('1')}],
        )
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_a_manager_correcting_a_job_is_held_to_the_same_rule(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('job_edit', args=[work_order.pk]),
            job_payload(
                [
                    {'material': self.material_raw, 'quantity': Decimal('2')},
                    {'material': self.material_raw, 'quantity': Decimal('3')},
                ],
                [{'material': self.material_finished, 'quantity': Decimal('5')}],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'je v této sekci vybraný víckrát')
        # The job still holds what it held before the refused correction.
        self.assertEqual(work_order.movements.count(), 2)

    # ------------------------------------------------ not offered a second time

    def test_a_new_row_is_not_offered_what_another_row_took(self):
        """„+ další řádek" re-renders the page, which is when the list is built."""
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        data['add_consumed'] = ''
        response = self.client.post(reverse('transform_create'), data)
        new_row = response.context['consumed_formset'].forms[1]
        self.assertNotIn(self.material_raw, new_row.fields['material'].queryset)
        self.assertIn(self.material_finished, new_row.fields['material'].queryset)

    def test_the_row_that_took_it_keeps_it(self):
        """Otherwise the option it has to re-select would not be in its own list,
        and the row would come back blank."""
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        data['add_consumed'] = ''
        response = self.client.post(reverse('transform_create'), data)
        first_row = response.context['consumed_formset'].forms[0]
        self.assertIn(self.material_raw, first_row.fields['material'].queryset)
        self.assertContains(response, f'value="{self.material_raw.pk}" selected')

    def test_the_two_material_sections_do_not_hide_from_each_other(self):
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        data['add_produced'] = ''
        response = self.client.post(reverse('transform_create'), data)
        new_row = response.context['produced_formset'].forms[1]
        self.assertIn(self.material_raw, new_row.fields['material'].queryset)

    def test_a_new_machine_row_is_not_offered_the_machine_already_running(self):
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2')}],
        )
        data['add_machines'] = ''
        response = self.client.post(reverse('transform_create'), data)
        new_row = response.context['machine_formset'].forms[1]
        self.assertNotIn(self.machine_a, new_row.fields['machine'].queryset)
        self.assertIn(self.machine_b, new_row.fields['machine'].queryset)

    def test_a_new_worker_row_is_not_offered_someone_already_named(self):
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('2')}],
        )
        data['add_workers'] = ''
        response = self.client.post(reverse('transform_create'), data)
        new_row = response.context['worker_formset'].forms[1]
        self.assertNotIn(self.other_worker, new_row.fields['user'].queryset)

    def test_the_edit_forms_blank_row_hides_what_the_job_already_uses(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[work_order.pk]))
        formset = response.context['consumed_formset']
        self.assertIn(self.material_raw, formset.forms[0].fields['material'].queryset)
        self.assertNotIn(self.material_raw, formset.forms[1].fields['material'].queryset)

    def test_a_fresh_form_hides_nothing(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        choices = response.context['consumed_formset'].forms[0].fields['material'].queryset
        self.assertIn(self.material_raw, choices)
        self.assertIn(self.material_finished, choices)


class RowButtonTests(ReviewFixtureMixin, TestCase):
    """„+ další řádek" and „− odebrat řádek" resize one section of the job form.

    A formset renders a fixed number of rows, so before these the form was a hard
    cap on what could be recorded: a job crushing one input into four fractions
    did not fit, and the mass balance meant it could not be split across two
    submissions either. Each button posts the form back under `add_<prefix>` or
    `remove_<prefix>` and the view re-renders it unbound, one row bigger or
    smaller in that section.

    Unbound is what most of this asserts. Asking for a different number of rows
    is not submitting the form, so the page has to come back carrying what was
    typed and complaining about nothing.
    """

    def _press(self, button, url=None, consumed=None, produced=None, **overrides):
        """Press one row button on an otherwise ordinary, balanced form."""
        data = job_payload(
            consumed or [{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced or [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        data.update(overrides)
        data[button] = ''
        return self.client.post(url or reverse('transform_create'), data)

    def _three_consumed(self):
        """Three consumed rows with quantities that cannot be confused for anything
        else on the page — the hours field and the produced row both hold small
        numbers."""
        return [{'material': self.material_raw, 'quantity': q} for q in ('11', '22', '33')]

    # ---------------------------------------------------------------- adding

    def test_the_named_section_gains_a_row_and_the_others_do_not(self):
        self.client.force_login(self.worker)
        response = self._press('add_consumed')
        # The typed row plus the blank one just added; every other section keeps
        # the single row `job_payload` sends and gains nothing.
        self.assertEqual(len(response.context['consumed_formset'].forms), 2)
        self.assertEqual(len(response.context['produced_formset'].forms), 1)
        self.assertEqual(len(response.context['machine_formset'].forms), 1)
        self.assertEqual(len(response.context['worker_formset'].forms), 1)

    def test_the_new_row_is_reachable_in_the_rendered_form(self):
        """The point of the whole feature: a fourth row the browser can post."""
        self.client.force_login(self.worker)
        # Three different fractions: a section refuses the same material twice,
        # which is the shape a real four-row job has anyway.
        third = Material.objects.create(sku='FIN2', name='Frakce 16/32')
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [
                {'material': self.material_finished, 'quantity': Decimal('2')},
                {'material': third, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('1')},
            ],
        )
        data['add_produced'] = ''
        response = self.client.post(reverse('transform_create'), data)
        self.assertContains(response, 'produced-3-material')
        self.assertContains(response, 'produced-3-quantity')
        self.assertEqual(response.context['produced_formset'].management_form['TOTAL_FORMS'].value(), 4)

    def test_what_was_typed_comes_back(self):
        self.client.force_login(self.worker)
        response = self._press('add_produced', description='Drcení na frakce', hours='7')
        self.assertContains(response, 'value="Drcení na frakce"')
        self.assertContains(response, 'value="7"')
        # The material already chosen is re-selected, not reset to the prompt.
        self.assertContains(response, f'value="{self.material_raw.pk}" selected')

    def test_the_date_comes_back_as_iso_rather_than_czech(self):
        """`<input type="date">` reads only ISO, and `cs` formats dates `05.09.2026`.

        The rebuilt form is unbound, which is the case
        `WorkOrderForm.performed_on` carries an explicit `format` for — here the
        initial is the raw POST string, which passes through untouched. A
        regression shows up as a silently blank date box, not as an error.
        """
        self.client.force_login(self.worker)
        today = timezone.localdate().isoformat()
        response = self._press('add_machines', performed_on=today)
        self.assertContains(response, f'value="{today}"')

    def test_nothing_is_written(self):
        self.client.force_login(self.worker)
        self._press('add_consumed')
        self.assertEqual(WorkOrder.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_an_unfinished_form_is_not_scolded(self):
        """A bound rebuild would put „Toto pole je vyžadováno." over the hours
        box of somebody who has not reached it yet."""
        self.client.force_login(self.worker)
        response = self._press('add_consumed', hours='', description='')
        self.assertNotContains(response, 'Toto pole je vyžadováno.')
        self.assertNotContains(response, 'Přidejte alespoň jednu položku')
        self.assertEqual(list(response.context['messages']), [])

    def test_an_unbalanced_form_is_not_scolded_either(self):
        self.client.force_login(self.worker)
        response = self._press('add_produced', **{'produced-0-quantity': '9'})
        self.assertNotContains(response, 'se musí rovnat')
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_the_collaborator_list_follows_zapsat_za(self):
        """The author decides who may be named, so growing has to resolve it.

        A manager recording for a worker must not get their own name back in the
        list — and a collaborator already picked would drop off the re-rendered
        row if the queryset it belongs to changed underneath it.
        """
        self.client.force_login(self.manager)
        response = self._press('add_workers', author=str(self.worker.pk))
        choices = response.context['worker_formset'].forms[0].fields['user'].queryset
        self.assertNotIn(self.worker, choices)
        self.assertIn(self.manager, choices)
        self.assertIn(self.other_worker, choices)
        self.assertContains(response, f'value="{self.worker.pk}" selected')

    def test_a_worker_growing_their_own_form_keeps_the_workers_only_list(self):
        self.client.force_login(self.worker)
        response = self._press('add_workers')
        choices = response.context['worker_formset'].forms[0].fields['user'].queryset
        self.assertNotIn(self.worker, choices)
        self.assertNotIn(self.manager, choices)
        self.assertIn(self.other_worker, choices)

    def test_a_tampered_row_count_does_not_blow_up(self):
        """TOTAL_FORMS is read straight out of the POST here, so it is untrusted."""
        self.client.force_login(self.worker)
        response = self._press('add_consumed', **{'consumed-TOTAL_FORMS': 'není číslo'})
        self.assertEqual(response.status_code, 200)
        # Nothing to restore, so the section comes back as its new blank row.
        self.assertEqual(len(response.context['consumed_formset'].forms), 1)

    def test_an_absurd_row_count_is_capped(self):
        self.client.force_login(self.worker)
        response = self._press('add_consumed', **{'consumed-TOTAL_FORMS': '999999'})
        # Exactly the cap, not the cap plus the new blank row: at the ceiling
        # Django will not add an extra beyond its own max_num, which is the same
        # 1000. Nothing legitimate reaches this, and a form that did would be
        # refused by the formset on submit anyway.
        self.assertEqual(len(response.context['consumed_formset'].forms), MAX_ROWS_PER_SECTION)

    def test_job_edit_grows_the_same_way(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self._press('add_machines', url=reverse('job_edit', args=[work_order.pk]))
        self.assertEqual(len(response.context['machine_formset'].forms), 2)
        self.assertEqual(len(response.context['consumed_formset'].forms), 1)

    def test_job_edit_saves_nothing_while_growing(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        self._press('add_consumed', url=reverse('job_edit', args=[work_order.pk]), description='Nemá se uložit')
        work_order.refresh_from_db()
        self.assertNotEqual(work_order.description, 'Nemá se uložit')

    def test_job_edit_keeps_the_authors_name_on_the_hours_label(self):
        """The label override applies on every branch, the grown one included."""
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self._press('add_workers', url=reverse('job_edit', args=[work_order.pk]))
        self.assertEqual(response.context['order_form'].fields['hours'].label, f'Hodiny – {self.worker.username}')

    def test_enter_still_saves_rather_than_adding_a_row(self):
        """A form is submitted through its *first* submit button when Enter is
        pressed in a text field. „+ další řádek" comes before the real button,
        so both pages open their <form> with an off-screen decoy that posts no
        name — without it, Enter in Popis would add a collaborator row."""
        self.client.force_login(self.worker)
        body = self.client.get(reverse('transform_create')).content.decode()
        self.assertLess(body.index('visually-hidden'), body.index('name="add_workers"'))

    # -------------------------------------------------------------- removing

    def test_the_named_section_loses_a_row_and_the_others_do_not(self):
        self.client.force_login(self.worker)
        response = self._press('remove_consumed', consumed=self._three_consumed())
        self.assertEqual(len(response.context['consumed_formset'].forms), 2)
        self.assertEqual(len(response.context['produced_formset'].forms), 1)
        self.assertEqual(len(response.context['machine_formset'].forms), 1)
        self.assertEqual(len(response.context['worker_formset'].forms), 1)

    def test_the_row_that_goes_is_the_last_one(self):
        """The exact row „+ další řádek" would have added, so the two buttons
        undo each other."""
        self.client.force_login(self.worker)
        response = self._press('remove_consumed', consumed=self._three_consumed())
        self.assertContains(response, 'value="11"')
        self.assertContains(response, 'value="22"')
        self.assertNotContains(response, 'value="33"')

    def test_the_last_row_is_never_removed(self):
        """A section with one row left stays at one, however often it is pressed.

        The button is hidden at that point, so this is the POST arriving anyway —
        a double tap on a slow connection, or a hand-edited form.
        """
        self.client.force_login(self.worker)
        response = self._press('remove_consumed')
        self.assertEqual(len(response.context['consumed_formset'].forms), MIN_ROWS_PER_SECTION)

    def test_the_remove_button_is_hidden_on_a_section_down_to_one_row(self):
        """Rendered `hidden`, not left out: `job_rows.js` toggles it back when
        it grows the section, so the element has to be there to toggle.

        With the script off this is the same experience as omitting it — the
        UA stylesheet hides `[hidden]` and nothing in app.css sets a `display`
        on `.row-controls button` to override that. The floor itself is
        `_resized_section`'s, not this attribute's; see
        `test_the_last_row_is_never_removed`.
        """
        self.client.force_login(self.worker)
        # Two machine rows, so that section still has one to spare and its own
        # button stays up — hiding is per section, not per page.
        response = self._press('remove_consumed', **{'machines-TOTAL_FORMS': '2'})
        self.assertContains(response, 'name="remove_consumed" data-remove-row formnovalidate hidden')
        # The way back is still offered, and the roomier section keeps both.
        self.assertContains(response, 'name="add_consumed"')
        self.assertContains(response, 'name="remove_machines" data-remove-row formnovalidate>')

    def test_no_section_ever_comes_back_empty(self):
        """The floor holds for the sections nobody pressed, too — otherwise a
        hand-edited TOTAL_FORMS would leave a bare heading with no row under it."""
        self.client.force_login(self.worker)
        response = self._press('add_consumed', **{'produced-TOTAL_FORMS': '0'})
        self.assertEqual(len(response.context['produced_formset'].forms), MIN_ROWS_PER_SECTION)

    def test_removing_writes_nothing_and_scolds_nobody(self):
        self.client.force_login(self.worker)
        response = self._press('remove_produced', consumed=self._three_consumed(), hours='')
        self.assertNotContains(response, 'Toto pole je vyžadováno.')
        self.assertNotContains(response, 'se musí rovnat')
        self.assertEqual(WorkOrder.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_removing_keeps_the_rest_of_the_form(self):
        self.client.force_login(self.worker)
        response = self._press(
            'remove_machines',
            consumed=self._three_consumed(),
            description='Drcení na frakce',
        )
        self.assertContains(response, 'value="Drcení na frakce"')
        self.assertContains(response, f'value="{self.material_raw.pk}" selected')
        self.assertContains(response, f'value="{timezone.localdate().isoformat()}"')

    def test_job_edit_removes_the_same_way(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self._press(
            'remove_consumed',
            url=reverse('job_edit', args=[work_order.pk]),
            consumed=self._three_consumed(),
        )
        self.assertEqual(len(response.context['consumed_formset'].forms), 2)
        work_order.refresh_from_db()
        # The job still has the one line item it was recorded with.
        self.assertEqual(work_order.movements.count(), 2)


class RowTemplateTests(ReviewFixtureMixin, TestCase):
    """The contract `static/js/job_rows.js` clones a row from.

    The script itself is not tested and cannot be: the suite drives views
    through `self.client` and there is no browser runner in this project, which
    is exactly why the server round-trip above stays and why the script is only
    ever an enhancement on top of it. What *is* testable is everything the
    script reads off the page, and that is what breaks silently — a renamed
    attribute or an `empty_form` that stopped receiving its `form_kwargs` makes
    „+ další řádek" quietly stop adding rows, or add ones with the wrong
    dropdown, with nothing failing anywhere.
    """

    def _page(self, user=None):
        self.client.force_login(user or self.worker)
        return self.client.get(reverse('transform_create'))

    def test_every_section_renders_a_row_template(self):
        response = self._page()
        for prefix, field in (
            ('workers', 'user'),
            ('consumed', 'material'),
            ('produced', 'material'),
            ('machines', 'machine'),
        ):
            self.assertContains(response, f'{prefix}-__prefix__-{field}')

    def test_the_template_is_inert_until_a_copy_is_inserted(self):
        """It sits inside the <form>, so if it were ordinary markup the browser
        would post `__prefix__` rows and the formset would choke on them."""
        response = self._page()
        self.assertContains(response, '<template data-row-template>')

    def test_the_row_bounds_are_rendered_rather_than_restated(self):
        """The floor and the cap the script honours are the view's own
        constants, so the two implementations cannot drift apart."""
        response = self._page()
        self.assertContains(
            response,
            f'data-job-section data-min-rows="{MIN_ROWS_PER_SECTION}" data-max-rows="{MAX_ROWS_PER_SECTION}"',
            count=len(JOB_SECTIONS),
        )

    def test_the_template_row_is_not_narrowed_by_the_hiding(self):
        """`_hide_taken_choices` touches `formset.forms` and must not reach
        `empty_form`: a row the script adds has to offer the whole catalog, and
        it is the script that hides from it live."""
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['consumed_formset']
        # The spare row is narrowed, as DuplicateRowTests asserts.
        self.assertNotIn(self.material_raw, formset.forms[1].fields['material'].queryset)
        self.assertIn(self.material_raw, formset.empty_form.fields['material'].queryset)

    def test_the_template_row_gets_the_collaborator_pair(self):
        """`empty_form` is built from the formset's `form_kwargs`, so a manager
        recording for a worker gets a template row scoped to that author — not
        one offering the author themselves."""
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('transform_create'),
            dict(
                job_payload(
                    [{'material': self.material_raw, 'quantity': Decimal('5')}],
                    [{'material': self.material_finished, 'quantity': Decimal('5')}],
                ),
                add_workers='',
                author=str(self.worker.pk),
            ),
        )
        choices = response.context['worker_formset'].empty_form.fields['user'].queryset
        self.assertNotIn(self.worker, choices)
        self.assertIn(self.manager, choices)
        self.assertIn(self.other_worker, choices)

    def test_the_template_row_honours_keep_on_the_edit_form(self):
        """Same `keep` the real rows get, so a retired record the job names
        stays reachable on a row the script adds."""
        work_order = self.submit_job(self.worker)
        self.material_raw.is_active = False
        self.material_raw.save(update_fields=['is_active'])
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['consumed_formset']
        self.assertIn(self.material_raw, formset.empty_form.fields['material'].queryset)

    def test_both_job_pages_load_the_scripts(self):
        """They are linked from the shared partial, so neither page can lose
        one — and a page carrying job_rows.js without searchable_select.js
        would add rows whose dropdowns are the only ones you cannot search."""
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        for url in (reverse('transform_create'), reverse('job_edit', args=[work_order.pk])):
            response = self.client.get(url)
            for script in ('js/job_rows.js', 'js/searchable_select.js'):
                self.assertContains(response, script)

    def test_no_script_reaches_for_anything_off_the_server(self):
        """The htmx <script> this app used to carry pointed at unpkg.com, which
        the depot could not necessarily reach. Committed files, no CDN."""
        for name in ('job_rows.js', 'searchable_select.js'):
            source = (settings.BASE_DIR / 'static' / 'js' / name).read_text(encoding='utf-8')
            self.assertNotIn('http://', source, name)
            self.assertNotIn('https://', source, name)

    def test_the_stylesheet_actually_hides_a_hidden_button(self):
        """`button, .btn` sets a `display`, and an author rule beats the UA
        stylesheet's `[hidden] { display: none }` at any specificity — so
        without an explicit rule the `hidden` on „− odebrat řádek" does
        nothing and a section down to one row renders a button that cannot
        lawfully do anything. It was broken exactly that way once; the browser
        is the only place it shows, so this stands in for looking.
        """
        css = (settings.BASE_DIR / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'button\[hidden\][^{]*\{[^}]*display:\s*none')


class SearchablePickerTests(ReviewFixtureMixin, TestCase):
    """The contract `static/js/searchable_select.js` reads off a row.

    Like job_rows.js it is enhancement and, for the same reason, untested in
    itself: there is no browser runner here. What it reads off the page is
    testable, and it is what breaks silently — the script hides each row's
    `<select>` behind a text box, so a row that stopped matching its
    assumptions loses its picker altogether rather than raising anywhere.
    """

    def _page(self):
        self.client.force_login(self.worker)
        return self.client.get(reverse('transform_create'))

    def test_every_row_has_exactly_one_select(self):
        """`.item-row select` is how the script finds the picker; a second
        dropdown in a row would silently get one too, over a field whose
        `empty_label` is not a placeholder."""
        for section in JOB_SECTIONS:
            pickers = [
                name for name, field in section.row_form().fields.items() if isinstance(field, forms.ModelChoiceField)
            ]
            self.assertEqual(len(pickers), 1, section.prefix)

    def test_the_first_option_is_the_empty_label(self):
        """It carries no value and is the row's only label (see
        `EmptyLabelTests`), so the script lifts it out as the text box's
        placeholder rather than offering it as something to pick."""
        response = self._page()
        for label in ('Pracovník', 'Materiál', 'Stroj'):
            self.assertContains(response, f'<option value="" selected>{label}</option>')

    def test_every_row_select_is_identified(self):
        """The listbox id is derived from the select's, so two rows on one page
        cannot end up sharing one."""
        response = self._page()
        for prefix, field in (
            ('workers', 'user'),
            ('consumed', 'material'),
            ('produced', 'material'),
            ('machines', 'machine'),
        ):
            self.assertContains(response, f'id="id_{prefix}-0-{field}"')

    def test_the_stylesheet_carries_the_picker_rules(self):
        """The list is absolutely positioned against `.combo`; without the
        stylesheet it renders as a bullet list pushing the rows below it down,
        and the hidden select reappears under it. The browser is the only place
        that shows, so this stands in for looking."""
        css = (settings.BASE_DIR / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'\.combo\s*\{[^}]*position:\s*relative')
        self.assertRegex(css, r'\.combo-list\s*\{[^}]*position:\s*absolute')
        self.assertRegex(css, r'\.combo select\[hidden\][^{]*\{[^}]*display:\s*none')


class ThemeTokenTests(TestCase):
    """Dark mode is a second set of values for one set of names.

    Every colour in `static/css/app.css` is a custom property declared twice:
    once in `:root` and once in the `prefers-color-scheme: dark` override. The
    failure mode is silent both ways round. A token declared only in the light
    block keeps its light value on a dark screen, and a mistyped `var()` name
    resolves to nothing at all; neither raises, and neither shows up in any
    test that drives a view, because the stylesheet is an external file that
    the test client never fetches or parses.
    """

    # The brand green is the same colour in both themes on purpose, and the
    # focus ring is that green at low alpha, so these five are declared once.
    SHARED = {
        '--brand-dark',
        '--brand-green',
        '--brand-green-dark',
        '--brand-green-text',
        '--focus-ring',
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        css = (settings.BASE_DIR / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        dark_at = css.index('@media (prefers-color-scheme: dark)')
        cls.css = css
        cls.light = set(re.findall(r'^\s*(--[\w-]+):', css[:dark_at], re.MULTILINE))
        cls.dark = set(
            re.findall(r'^\s*(--[\w-]+):', css[dark_at : css.index('* { box-sizing', dark_at)], re.MULTILINE)
        )
        cls.used = set(re.findall(r'var\((--[\w-]+)\)', css))

    def test_every_light_token_has_a_dark_counterpart(self):
        self.assertEqual(self.light - self.dark, self.SHARED)

    def test_the_dark_block_introduces_no_token_of_its_own(self):
        self.assertEqual(self.dark - self.light, set())

    def test_every_referenced_token_is_declared(self):
        self.assertEqual(self.used - self.light - self.dark, set())

    def test_no_rule_hardcodes_a_colour_outside_the_token_blocks(self):
        rules = self.css[self.css.index('* { box-sizing') :]
        literals = re.findall(r'#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)', rules)
        self.assertEqual(literals, [])


class StockMovementTestCase(TestCase):
    def setUp(self):
        self.material = Material.objects.create(sku='SKU1', name='Steel Bar')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.work_order = WorkOrder.objects.create(created_by=self.worker, description='job')

    def _line_item(self, quantity, movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE, **kwargs):
        kwargs.setdefault('work_order', self.work_order)
        return StockMovement.objects.create(
            material=self.material,
            quantity=quantity,
            movement_type=movement_type,
            created_by=self.worker,
            **kwargs,
        )


class StockMovementModelTests(StockMovementTestCase):
    """StockMovement is a job line item now — what a transformation consumed or
    produced. Nothing sums these into a balance and nothing checks sufficiency
    before writing one, so the model's job is just to record the row faithfully.
    """

    def test_consumed_quantity_is_stored_negative(self):
        movement = self._line_item(Decimal('-5'), StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(movement.quantity, Decimal('-5.000'))

    def test_produced_quantity_is_stored_positive(self):
        movement = self._line_item(Decimal('5'))
        self.assertEqual(movement.quantity, Decimal('5.000'))

    def test_movement_type_labels_are_czech(self):
        consumed = self._line_item(Decimal('-1'), StockMovement.MovementType.TRANSFORM_CONSUME)
        produced = self._line_item(Decimal('1'))
        self.assertEqual(consumed.get_movement_type_display(), 'Spotřeba')
        self.assertEqual(produced.get_movement_type_display(), 'Výroba')

    def test_only_the_two_transform_types_remain(self):
        # Receipt, shipment and adjustment went with stock tracking.
        self.assertEqual(
            [value for value, _ in StockMovement.MovementType.choices],
            ['TRANSFORM_CONSUME', 'TRANSFORM_PRODUCE'],
        )

    def test_a_line_item_has_no_timestamp_of_its_own(self):
        # Its date is the job's `performed_on`. The two timestamps it used to
        # carry were never read.
        field_names = {f.name for f in StockMovement._meta.get_fields()}
        self.assertNotIn('created_at', field_names)
        self.assertNotIn('recorded_at', field_names)

    def test_ordering_is_the_order_the_rows_were_typed(self):
        first = self._line_item(Decimal('1'))
        second = self._line_item(Decimal('1'))
        self.assertEqual(list(StockMovement.objects.all()), [first, second])


class StockMovementWorkOrderTests(StockMovementTestCase):
    def test_line_items_are_reachable_from_their_work_order(self):
        work_order = WorkOrder.objects.create(created_by=self.worker, description='Drcení')
        self._line_item(Decimal('-5'), StockMovement.MovementType.TRANSFORM_CONSUME, work_order=work_order)
        self._line_item(Decimal('3'), work_order=work_order)
        self.assertEqual(work_order.movements.count(), 2)

    def test_a_line_item_cannot_exist_without_a_job(self):
        self.assertFalse(StockMovement._meta.get_field('work_order').null)


class RetiredCatalogOnEditTests(ReviewFixtureMixin, TestCase):
    """A job that names a material or machine retired *after* it was recorded
    stays editable, and editing it does not quietly throw the row away.

    The row pickers are scoped to the active catalog so a retired record cannot
    land on a new job. That scoping used to apply to `job_edit` as well, where
    the stored pk is not in the queryset: the select rendered with nothing
    chosen, and saving the form as rendered posted an empty row. A machine usage
    was deleted outright and the edit still reported success; a consumed row took
    the whole job into a mass-balance error that no edit could clear, which made
    the job permanently uneditable.
    """

    def _retire(self, record):
        record.is_active = False
        record.save(update_fields=['is_active'])

    def test_retired_machine_still_renders_on_the_row_it_is_on(self):
        job = self.submit_job(
            self.worker,
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}],
        )
        self._retire(self.machine_a)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[job.pk]))
        row = response.context['machine_formset'].forms[0]
        self.assertIn(self.machine_a, row.fields['machine'].queryset)
        self.assertIn('Crusher A', response.content.decode())

    def test_retiring_a_machine_does_not_delete_its_usage_on_the_next_edit(self):
        job = self.submit_job(
            self.worker,
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}],
        )
        self._retire(self.machine_a)
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}],
            description='opraveno',
        )
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_edit', args=[job.pk]), payload)
        self.assertRedirects(response, reverse('job_detail', args=[job.pk]))
        usage = MachineUsage.objects.get(work_order=job)
        self.assertEqual(usage.machine, self.machine_a)
        self.assertEqual(usage.hours, Decimal('2.00'))

    def test_a_job_naming_a_retired_material_can_still_be_corrected(self):
        job = self.submit_job(self.worker)
        self._retire(self.material_raw)
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            description='opraveno',
        )
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_edit', args=[job.pk]), payload)
        self.assertRedirects(response, reverse('job_detail', args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.description, 'opraveno')
        consumed = job.movements.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.material, self.material_raw)

    def test_a_retired_record_is_still_kept_off_a_new_job(self):
        # The widening is scoped to the job being edited; recording a *new* job
        # must not offer a retired material, which is the whole point of
        # retiring it.
        self._retire(self.material_raw)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        row = response.context['consumed_formset'].forms[0]
        self.assertNotIn(self.material_raw, row.fields['material'].queryset)

    def test_the_edit_form_offers_no_other_retired_record(self):
        # Only what this job already names comes back, not the retired catalog.
        other = Material.objects.create(sku='OLD', name='Vyřazený písek', is_active=False)
        job = self.submit_job(self.worker)
        self._retire(self.material_raw)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[job.pk]))
        queryset = response.context['consumed_formset'].forms[0].fields['material'].queryset
        self.assertIn(self.material_raw, queryset)
        self.assertNotIn(other, queryset)


class RowErrorVisibilityTests(ReviewFixtureMixin, TestCase):
    """Nothing is written unless every form validates, so an error with nowhere
    to render is a submission that vanishes without a word."""

    def _submit(self, **overrides):
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}],
        )
        payload.update(overrides)
        self.client.force_login(self.worker)
        return self.client.post(reverse('transform_create'), payload)

    def test_a_bad_quantity_says_what_is_wrong_with_it(self):
        response = self._submit(**{'consumed-0-quantity': '5.123'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'desetinná místa')

    def test_a_field_error_does_not_also_claim_the_row_is_half_empty(self):
        # The row *was* filled in; the value simply did not validate. The old
        # behaviour hid the real message and printed this one instead.
        response = self._submit(**{'consumed-0-quantity': '5.123'})
        self.assertNotContains(response, 'Vyplňte materiál i množství')

    def test_motohodiny_off_the_step_say_so(self):
        response = self._submit(**{'machines-0-hours': '0.3'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertNotContains(response, 'Vyplňte motohodiny i tuny')
        self.assertContains(response, 'Hodiny:')

    def test_a_genuinely_half_filled_row_still_says_so(self):
        # The guard must not swallow the rule it sits in front of: a row with a
        # material and no quantity has no field error of its own.
        response = self._submit(**{'consumed-0-quantity': ''})
        self.assertContains(response, 'Vyplňte materiál i množství')

    def test_row_errors_render_on_the_edit_form_too(self):
        # Both pages share `_job_form_fields.html`, so this cannot regress on
        # one of them alone.
        job = self.submit_job(self.worker)
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        payload['consumed-0-quantity'] = '5.123'
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_edit', args=[job.pk]), payload)
        self.assertContains(response, 'desetinná místa')


class DescriptionAndNotesTests(ReviewFixtureMixin, TestCase):
    """„Popis" is required, „Poznámky" is optional and holds a paragraph.

    Both render on the shared partial, so both pages get them at once; the
    required one has to be refused with a message rather than stored blank. The
    one submission „Popis" is not asked of is a fuel-only job, which has no work
    to label — see MachineRefuelTests.
    """

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
        # The point of the field: `description` is capped at 255, this is not.
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


class HoursWidthTests(ReviewFixtureMixin, TestCase):
    """The hours form fields must not accept more than their columns hold.

    `WorkerHours.hours` and `MachineUsage.hours` are `decimal(12, 2)`, so ten
    integer digits. A form field wider than that validated the value, reached
    the INSERT and came back as `DataError: numeric field overflow` — an
    unhandled 500 rather than a message on the field.
    """

    def _submit(self, **overrides):
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}],
        )
        payload.update(overrides)
        self.client.force_login(self.worker)
        return self.client.post(reverse('transform_create'), payload)

    def test_own_hours_too_wide_are_refused_by_the_form(self):
        response = self._submit(hours='99999999999.5')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_machine_hours_too_wide_are_refused_by_the_form(self):
        response = self._submit(**{'machines-0-hours': '99999999999.5'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MachineUsage.objects.exists())

    def test_collaborator_hours_too_wide_are_refused_by_the_form(self):
        response = self._submit(
            **{
                'workers-TOTAL_FORMS': '1',
                'workers-0-user': str(self.other_worker.pk),
                'workers-0-hours': '99999999999.5',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkerHours.objects.exists())

    def test_the_widest_value_the_form_accepts_actually_stores(self):
        # The cap has to land exactly on what the column holds: one digit less
        # and a legitimate value is refused, one more and this is a 500. Ten
        # integer digits and the one decimal place the field allows is the
        # widest `decimal(12, 2)` takes.
        response = self._submit(hours='9999999999.5')
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkerHours.objects.get(user=self.worker).hours, Decimal('9999999999.50'))


class MachineRefuelTests(ReviewFixtureMixin, TestCase):
    """Fuel is a fourth record type, in its own table, on the machines section.

    Three things to hold. A machine row's two halves are independent: motohodiny
    and tuny still go together, litres stand alone, and one row can carry both.
    A submission that is nothing but fill-ups is a whole valid job, which means
    it is exempt from the mass balance and from the two job fields every other
    job must answer. And `job_edit` has to put both halves back on one row per
    machine, because the section refuses a machine twice.
    """

    def _submit(self, machine_rows, url=None, **overrides):
        """A balanced job carrying `machine_rows`, unless a test empties the material sections."""
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=machine_rows,
        )
        payload.update(overrides)
        return self.client.post(url or reverse('transform_create'), payload)

    def _fuel_only(self, machine_rows, url=None, **overrides):
        """Just machines and litres: no materials, no motohodiny, no „Popis", no hours."""
        payload = job_payload([], [], machine_rows=machine_rows, description='', hours='')
        payload.update(overrides)
        return self.client.post(url or reverse('transform_create'), payload)

    def test_a_machine_row_records_litres_alongside_hours(self):
        self.client.force_login(self.worker)
        response = self._submit(
            [{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': Decimal('40.5')}]
        )
        self.assertRedirects(response, reverse('transform_create'))
        # One row in, two tables out: the usage and the fill-up are not derived
        # from each other and nothing checks one against the other.
        self.assertEqual(MachineUsage.objects.get().hours, Decimal('2.00'))
        refuel = MachineRefuel.objects.get()
        self.assertEqual(refuel.machine, self.machine_a)
        self.assertEqual(refuel.litres, Decimal('40.50'))

    def test_a_row_naming_only_litres_records_no_usage(self):
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a, 'litres': Decimal('30')}])
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineRefuel.objects.get().litres, Decimal('30.00'))
        # A machine that was only refuelled did not run: no hours to invent.
        self.assertFalse(MachineUsage.objects.exists())

    def test_zero_litres_beside_a_usage_is_the_blank_box(self):
        # A worker who types 0 for „didn't refuel" means what an empty box means,
        # and must not be told to type at least 0,01 instead.
        self.client.force_login(self.worker)
        response = self._submit(
            [{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': '0'}]
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineUsage.objects.count(), 1)
        # No fill-up of nothing: the row is a usage row and only that.
        self.assertFalse(MachineRefuel.objects.exists())

    def test_zero_litres_alone_says_nothing(self):
        # Treated as blank all the way through, „stroj + 0" is a machine with
        # nothing beside it — not a fuel-only job that recorded nothing.
        self.client.force_login(self.worker)
        response = self._fuel_only([{'machine': self.machine_a, 'litres': '0'}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'natankované litry')

    def test_the_litres_box_allows_zero_but_not_less(self):
        self.client.force_login(self.worker)
        self.assertContains(self.client.get(reverse('transform_create')), 'min="0"')
        response = self._submit(
            [{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': '-1'}]
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_hours_without_tons_is_still_refused(self):
        # The usage half keeps its own all-or-nothing rule; litres relaxing the
        # row must not relax that too.
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': ''}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Vyplňte motohodiny i tuny')

    def test_litres_without_a_machine_is_refused(self):
        self.client.force_login(self.worker)
        response = self._submit([], **{'machines-0-litres': '30'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Vyberte stroj')

    def test_a_machine_with_nothing_beside_it_is_refused(self):
        # Naming a machine and leaving every number blank says nothing at all,
        # and would write neither a usage row nor a fill-up.
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'natankované litry')

    def test_a_job_of_nothing_but_fill_ups_is_valid(self):
        self.client.force_login(self.worker)
        response = self._fuel_only(
            [
                {'machine': self.machine_a, 'litres': Decimal('60')},
                {'machine': self.machine_b, 'litres': Decimal('45.5')},
            ]
        )
        self.assertRedirects(response, reverse('transform_create'))
        work_order = WorkOrder.objects.get()
        self.assertEqual(
            sorted((r.machine.name, r.litres) for r in work_order.machine_refuels.all()),
            [('Crusher A', Decimal('60.00')), ('Excavator B', Decimal('45.50'))],
        )
        # Nothing else is written — including no hours row for the author, whose
        # „Moje hodiny" could not have been answered (its minimum is half an hour).
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())
        self.assertEqual(work_order.description, '')
        # Still an ordinary job otherwise: dated, authored and awaiting review.
        self.assertEqual(work_order.created_by, self.worker)
        self.assertEqual(work_order.performed_on, timezone.localdate())
        self.assertEqual(work_order.status, WorkOrder.Status.PENDING)

    def test_a_fuel_only_job_needs_no_material_rows(self):
        """The mass balance is skipped rather than passed: there are no totals.

        Without the exemption „Přidejte alespoň jednu položku…" would refuse the
        submission, which is the whole reason `_is_fuel_only` exists.
        """
        self.client.force_login(self.worker)
        response = self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('20')}])
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineRefuel.objects.count(), 1)

    def test_a_job_that_also_records_work_still_needs_its_fields(self):
        # The exemption is only for a job that records nothing else. Add any
        # motohodiny and the row stops being a bare fill-up.
        self.client.force_login(self.worker)
        response = self._fuel_only(
            [{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': Decimal('20')}]
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Toto pole je třeba vyplnit.')
        self.assertContains(response, 'Přidejte alespoň jednu položku')

    def test_material_rows_without_a_description_are_still_refused(self):
        # Same rule from the other side: material rows are work, so the job's
        # own fields come back.
        self.client.force_login(self.worker)
        response = self._submit([], description='', hours='')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Toto pole je třeba vyplnit.')

    def test_a_fuel_only_job_may_still_carry_a_description(self):
        # Not asked for is not the same as refused — the four list pages read
        # that column, so a worker who types one keeps it.
        self.client.force_login(self.worker)
        response = self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('20')}], description='Tankování')
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkOrder.objects.get().description, 'Tankování')

    def test_the_same_machine_refuelled_twice_in_one_job_is_refused(self):
        # Same rule as the motohodiny: two rows for one machine are two halves
        # of a number that belongs on one row.
        self.client.force_login(self.worker)
        response = self._fuel_only(
            [
                {'machine': self.machine_a, 'litres': Decimal('20')},
                {'machine': self.machine_a, 'litres': Decimal('25')},
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MachineRefuel.objects.exists())
        self.assertContains(response, 'Sečtěte motohodiny, tuny a litry')

    def test_the_form_renders_the_litres_box_on_both_pages(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        for url in (reverse('transform_create'), reverse('job_edit', args=[work_order.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'name="machines-0-litres"')

    def test_edit_prefills_both_halves_on_one_row(self):
        work_order = self.submit_job(
            self.worker,
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': '40'}],
        )
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['machine_formset']
        self.assertEqual(
            formset.forms[0].initial,
            {'machine': self.machine_a.pk, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': Decimal('40')},
        )

    def test_edit_prefills_a_fill_up_that_has_no_usage_row(self):
        """A fuel-only job's row has litres and nothing else, and must come back
        that way — a row rendered blank would be saved as blank and the fill-up
        would vanish while the page reported success."""
        self.client.force_login(self.worker)
        self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('60')}])
        work_order = WorkOrder.objects.get()
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['machine_formset']
        self.assertEqual(formset.forms[0].initial, {'machine': self.machine_a.pk, 'litres': Decimal('60')})

    def test_editing_a_fuel_only_job_rewrites_its_litres(self):
        self.client.force_login(self.worker)
        self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('60')}])
        work_order = WorkOrder.objects.get()
        self.client.force_login(self.manager)
        response = self._fuel_only(
            [{'machine': self.machine_a, 'litres': Decimal('75')}],
            url=reverse('job_edit', args=[work_order.pk]),
        )
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))
        self.assertEqual(MachineRefuel.objects.get().litres, Decimal('75.00'))

    def test_edit_keeps_a_retired_machine_that_only_a_fill_up_names(self):
        """Same data-loss trap as the usage rows: a picker that excludes the
        stored machine renders the row empty and saving it drops the fill-up."""
        self.client.force_login(self.worker)
        self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('60')}])
        self.machine_a.is_active = False
        self.machine_a.save(update_fields=['is_active'])
        self.client.force_login(self.manager)
        work_order = WorkOrder.objects.get()
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['machine_formset']
        self.assertIn(self.machine_a, formset.forms[0].fields['machine'].queryset)

    def test_deleting_the_job_takes_its_fill_ups_with_it(self):
        self.client.force_login(self.worker)
        self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('60')}])
        work_order = WorkOrder.objects.get()
        self.client.force_login(self.manager)
        self.client.post(reverse('job_delete', args=[work_order.pk]))
        self.assertFalse(MachineRefuel.objects.exists())

    def test_the_job_pages_show_the_fill_ups(self):
        work_order = self.submit_job(
            self.worker,
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': '40'}],
        )
        self.client.force_login(self.manager)
        for name in ('job_detail', 'job_delete'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=[work_order.pk]))
                self.assertContains(response, 'Tankování')


class MachineRefuelReportTests(TestCase):
    """The „Tankování" card on Stroje: per-machine litres, the fill-ups, the CSV.

    It answers to the page's one filter card, counts only approved jobs, and is
    gated exactly like the rest of Stroje.
    """

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.other = Machine.objects.create(name='Excavator B')
        self.retired = Machine.objects.create(name='Old Loader', is_active=False)
        self.client.force_login(self.manager)

    def _refuel(self, litres, machine=None, performed_on=None, status=WorkOrder.Status.APPROVED):
        work_order = WorkOrder.objects.create(created_by=self.worker, description='job', status=status)
        if performed_on is not None:
            WorkOrder.objects.filter(pk=work_order.pk).update(performed_on=performed_on)
        return MachineRefuel.objects.create(work_order=work_order, machine=machine or self.machine, litres=litres)

    def _summary(self, **params):
        return {
            row.name: row for row in self.client.get(reverse('machine_dashboard'), params).context['refuel_machines']
        }

    def test_litres_are_summed_per_machine(self):
        self._refuel(Decimal('40'))
        self._refuel(Decimal('25.5'))
        self._refuel(Decimal('10'), machine=self.other)
        rows = self._summary()
        self.assertEqual(rows['Crusher A'].filtered_litres, Decimal('65.50'))
        self.assertEqual(rows['Crusher A'].filtered_refuels, 2)
        self.assertEqual(rows['Excavator B'].filtered_litres, Decimal('10.00'))

    def test_the_card_renders_the_czech_decimal_comma(self):
        self._refuel(Decimal('65.5'))
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, 'Tankování')
        self.assertContains(response, '65,50 l')

    def test_a_machine_never_refuelled_reads_a_real_zero(self):
        # Unlike the tonnage on the other card, litres are not nullable, so a
        # zero here means "not refuelled in range" and not "unknown".
        self._refuel(Decimal('40'))
        self.assertEqual(self._summary()['Excavator B'].filtered_litres, Decimal('0'))

    def test_retired_machines_are_left_out(self):
        self.assertNotIn('Old Loader', self._summary())

    def test_pending_jobs_are_invisible(self):
        self._refuel(Decimal('40'), status=WorkOrder.Status.PENDING)
        self.assertEqual(self._summary()['Crusher A'].filtered_litres, Decimal('0'))

    def test_the_date_filter_narrows_the_totals(self):
        # Dated by the job, like every other row type: `job_edit` rewrites the
        # rows, so their own insert time would drift to the day of a correction.
        self._refuel(Decimal('40'), performed_on=date.today() - timedelta(days=40))
        self._refuel(Decimal('15'))
        rows = self._summary(date_from=(date.today() - timedelta(days=7)).isoformat())
        self.assertEqual(rows['Crusher A'].filtered_litres, Decimal('15.00'))
        self.assertEqual(rows['Crusher A'].filtered_refuels, 1)

    def test_naming_a_machine_narrows_the_card_to_it(self):
        self._refuel(Decimal('40'))
        self.assertEqual(list(self._summary(machine=self.other.pk)), ['Excavator B'])

    def test_an_invalid_filter_shows_no_machines(self):
        self._refuel(Decimal('40'))
        response = self.client.get(reverse('machine_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(list(response.context['refuel_machines']), [])
        self.assertEqual(response.context['refuel_total_litres'], Decimal('0'))

    def test_the_total_follows_the_filter(self):
        self._refuel(Decimal('40'), performed_on=date.today() - timedelta(days=40))
        self._refuel(Decimal('15'))
        self._refuel(Decimal('5'), machine=self.other)
        response = self.client.get(
            reverse('machine_dashboard'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        self.assertEqual(response.context['refuel_total_litres'], Decimal('20.00'))

    def test_the_detail_table_lists_the_fill_ups(self):
        self._refuel(Decimal('40'))
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, 'Detail tankování')
        self.assertEqual(len(response.context['fuel_page']['page_obj'].object_list), 1)

    def test_each_detail_table_pages_on_its_own_parameter(self):
        """Stroje holds two paginated tables. One shared `page` would move both,
        so the fill-ups carry their own — and its links have to keep the other
        table's parameter and the filters."""
        for _ in range(HISTORY_PAGE_SIZE + 1):
            self._refuel(Decimal('1'))
        response = self.client.get(reverse('machine_dashboard'), {FUEL_PAGE_PARAM: '2', 'page': '1'})
        fuel_page = response.context['fuel_page']
        self.assertEqual(fuel_page['page_obj'].number, 2)
        self.assertEqual(fuel_page['page_param'], FUEL_PAGE_PARAM)
        # Its own parameter dropped, the other table's kept.
        self.assertNotIn(FUEL_PAGE_PARAM, fuel_page['querystring'])
        self.assertIn('page=1', fuel_page['querystring'])
        # And the usage table is unmoved by a fuel page link.
        self.assertEqual(response.context['page_obj'].number, 1)

    def test_a_date_preset_resets_both_tables_to_page_one(self):
        response = self.client.get(reverse('machine_dashboard'), {'page': '2', FUEL_PAGE_PARAM: '3'})
        for preset in response.context['date_presets']:
            with self.subTest(preset=preset['key']):
                self.assertNotIn('page=', preset['querystring'])

    def test_the_export_carries_the_summary_table(self):
        self._refuel(Decimal('40'))
        self._refuel(Decimal('25.5'))
        response = self.client.get(reverse('machine_refuel_export'))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(rows[0], ['Stroj', 'Počet tankování', 'Natankováno (l)'])
        self.assertIn(['Crusher A', '2', '65,50'], rows)
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="tankovani-{date.today().isoformat()}.csv"',
        )

    def test_the_export_follows_the_page_filter(self):
        self._refuel(Decimal('40'), performed_on=date.today() - timedelta(days=40))
        self._refuel(Decimal('15'))
        response = self.client.get(
            reverse('machine_refuel_export'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertIn(['Crusher A', '1', '15,00'], rows)

    def test_an_invalid_filter_exports_only_a_header(self):
        self._refuel(Decimal('40'))
        response = self.client.get(reverse('machine_refuel_export'), {'date_from': 'not-a-date'})
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(len(rows), 1)

    def test_a_worker_cannot_reach_the_export(self):
        # Gated like the page it hangs off; hiding the nav link is not access control.
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse('machine_refuel_export')).status_code, 403)


class LocationTests(ReviewFixtureMixin, TestCase):
    """Where a job was worked: a catalog managed in the admin, one per job.

    A label, not a stock location — nothing balances per location. Required on
    every job that records work, and exempt on a fuel-only one exactly like
    „Popis" and „Moje hodiny". Shown and filtered on Přehled and Materiál.
    """

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

    def test_fuel_only_job_needs_no_location(self):
        self.client.force_login(self.worker)
        payload = job_payload(
            [], [], machine_rows=[{'machine': self.machine_a, 'litres': Decimal('40')}], description='', hours=''
        )
        payload['location'] = ''
        self.assertRedirects(self.client.post(reverse('transform_create'), payload), reverse('transform_create'))
        self.assertIsNone(WorkOrder.objects.get().location)

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
