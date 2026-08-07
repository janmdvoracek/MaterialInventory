from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import StockMovement
from materials.models import Location, Material

from .models import WorkOrder


class TransformCreateTests(TestCase):
    def setUp(self):
        self.material_raw = Material.objects.create(sku='RAW', name='Raw Steel', unit_of_measure='kg')
        self.material_finished = Material.objects.create(sku='FIN', name='Bracket', unit_of_measure='pcs')
        self.location = Location.objects.create(name='Main Depot')
        self.worker = User.objects.create_user(username='worker', password='pw')

    def _seed_stock(self, material, quantity):
        StockMovement.objects.create(
            material=material,
            location=self.location,
            quantity=quantity,
            movement_type=StockMovement.MovementType.RECEIPT,
            created_by=self.worker,
        )

    def _post(self, consumed_rows, produced_rows, description='Test job'):
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
        }
        for i, row in enumerate(consumed_rows):
            data[f'consumed-{i}-material'] = row['material'].pk
            data[f'consumed-{i}-location'] = row['location'].pk
            data[f'consumed-{i}-quantity'] = str(row['quantity'])
        for i, row in enumerate(produced_rows):
            data[f'produced-{i}-material'] = row['material'].pk
            data[f'produced-{i}-location'] = row['location'].pk
            data[f'produced-{i}-quantity'] = str(row['quantity'])
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
