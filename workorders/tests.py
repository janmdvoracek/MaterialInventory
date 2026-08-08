from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import StockMovement
from materials.models import Location, Machine, Material

from .models import MachineUsage, WorkOrder


class TransformCreateTests(TestCase):
    def setUp(self):
        self.material_raw = Material.objects.create(sku='RAW', name='Raw Steel', unit_of_measure='kg')
        self.material_finished = Material.objects.create(sku='FIN', name='Bracket', unit_of_measure='pcs')
        self.location = Location.objects.create(name='Main Depot')
        self.worker = User.objects.create_user(username='worker', password='pw')
        self.machine_a = Machine.objects.create(name='Crusher A')
        self.machine_b = Machine.objects.create(name='Excavator B')

    def _seed_stock(self, material, quantity):
        StockMovement.objects.create(
            material=material,
            location=self.location,
            quantity=quantity,
            movement_type=StockMovement.MovementType.RECEIPT,
            created_by=self.worker,
        )

    def _post(self, consumed_rows, produced_rows, machine_rows=None, description='Test job'):
        machine_rows = machine_rows or []
        data = {
            'description': description,
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
        }
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
        self.client.force_login(self.worker)
        return self.client.post(reverse('transform_create'), data)

    def test_transform_create_requires_login(self):
        response = self.client.get(reverse('transform_create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_transform_produce_only_no_stock_check(self):
        response = self._post(
            [], [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}]
        )
        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(WorkOrder.objects.exists())
        movement = StockMovement.objects.get(movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE)
        self.assertEqual(movement.quantity, Decimal('3'))

    def test_transform_consume_rejected_with_no_stock(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('5')}], []
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(
            StockMovement.objects.filter(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME).exists()
        )

    def test_transform_consume_aggregates_same_material_location_across_rows(self):
        self._seed_stock(self.material_raw, Decimal('10'))
        response = self._post(
            [
                {'material': self.material_raw, 'location': self.location, 'quantity': Decimal('6')},
                {'material': self.material_raw, 'location': self.location, 'quantity': Decimal('6')},
            ],
            [],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(
            StockMovement.objects.filter(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME).exists()
        )

    def test_transform_consume_succeeds_at_exact_boundary(self):
        self._seed_stock(self.material_raw, Decimal('10'))
        response = self._post(
            [
                {'material': self.material_raw, 'location': self.location, 'quantity': Decimal('5')},
                {'material': self.material_raw, 'location': self.location, 'quantity': Decimal('5')},
            ],
            [],
        )
        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(WorkOrder.objects.exists())
        consumed = StockMovement.objects.filter(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.count(), 2)
        self.assertEqual(sum((m.quantity for m in consumed), Decimal('0')), Decimal('-10'))

    def test_transform_atomic_rollback_on_partial_shortfall(self):
        self._seed_stock(self.material_raw, Decimal('10'))
        response = self._post(
            [
                {'material': self.material_raw, 'location': self.location, 'quantity': Decimal('5')},
                {'material': self.material_finished, 'location': self.location, 'quantity': Decimal('5')},
            ],
            [],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(
            StockMovement.objects.filter(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME).exists()
        )

    def test_transform_with_single_machine_increments_total_hours(self):
        response = self._post(
            [],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2.5')}],
        )
        self.assertRedirects(response, reverse('dashboard'))
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('2.5'))
        usage = MachineUsage.objects.get()
        self.assertEqual(usage.machine, self.machine_a)
        self.assertEqual(usage.hours, Decimal('2.5'))
        self.assertEqual(usage.work_order, WorkOrder.objects.get())

    def test_transform_with_chained_machines_each_increment_independently(self):
        response = self._post(
            [],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('2')},
                {'machine': self.machine_b, 'hours': Decimal('1.5')},
            ],
        )
        self.assertRedirects(response, reverse('dashboard'))
        self.machine_a.refresh_from_db()
        self.machine_b.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('2'))
        self.assertEqual(self.machine_b.total_hours, Decimal('1.5'))
        work_order = WorkOrder.objects.get()
        self.assertEqual(MachineUsage.objects.filter(work_order=work_order).count(), 2)

    def test_transform_same_machine_used_twice_accumulates(self):
        response = self._post(
            [],
            [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('1')},
                {'machine': self.machine_a, 'hours': Decimal('1')},
            ],
        )
        self.assertRedirects(response, reverse('dashboard'))
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('2'))
        self.assertEqual(MachineUsage.objects.filter(machine=self.machine_a).count(), 2)

    def test_empty_machine_formset_is_optional(self):
        response = self._post(
            [], [{'material': self.material_finished, 'location': self.location, 'quantity': Decimal('3')}]
        )
        self.assertRedirects(response, reverse('dashboard'))
        self.assertFalse(MachineUsage.objects.exists())

    def test_machine_row_missing_hours_rejected(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            'consumed-TOTAL_FORMS': '1',
            'consumed-INITIAL_FORMS': '0',
            'consumed-MIN_NUM_FORMS': '0',
            'consumed-MAX_NUM_FORMS': '1000',
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
        }
        response = self.client.post(reverse('transform_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())

    def test_machine_row_missing_machine_rejected(self):
        self.client.force_login(self.worker)
        data = {
            'description': 'Test job',
            'consumed-TOTAL_FORMS': '1',
            'consumed-INITIAL_FORMS': '0',
            'consumed-MIN_NUM_FORMS': '0',
            'consumed-MAX_NUM_FORMS': '1000',
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
        }
        response = self.client.post(reverse('transform_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())

    def test_stock_shortfall_rolls_back_machine_usage_too(self):
        response = self._post(
            [{'material': self.material_raw, 'location': self.location, 'quantity': Decimal('5')}],
            [],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())
        self.machine_a.refresh_from_db()
        self.assertEqual(self.machine_a.total_hours, Decimal('0'))
