from decimal import Decimal

from django.db import transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from materials.models import Location, Material

from .models import StockMovement
from .services import get_available_quantity


class InventoryTestCase(TestCase):
    def setUp(self):
        self.material = Material.objects.create(sku='SKU1', name='Steel Bar', unit_of_measure='pcs')
        self.location = Location.objects.create(name='Main Depot')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)

    def _movement(self, quantity, movement_type=StockMovement.MovementType.RECEIPT, user=None):
        return StockMovement.objects.create(
            material=self.material,
            location=self.location,
            quantity=quantity,
            movement_type=movement_type,
            created_by=user or self.worker,
        )


class DashboardTests(InventoryTestCase):
    def test_dashboard_sums_movements_correctly(self):
        self._movement(Decimal('10'), StockMovement.MovementType.RECEIPT)
        self._movement(Decimal('-3'), StockMovement.MovementType.SHIPMENT)
        self._movement(Decimal('-1'), StockMovement.MovementType.ADJUSTMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('dashboard'))
        stock = list(response.context['stock'])
        self.assertEqual(len(stock), 1)
        self.assertEqual(stock[0]['quantity'], Decimal('6'))

    def test_dashboard_excludes_zero_or_negative_net_stock(self):
        self._movement(Decimal('5'), StockMovement.MovementType.RECEIPT)
        self._movement(Decimal('-5'), StockMovement.MovementType.SHIPMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(list(response.context['stock']), [])


class ReceiptCreateTests(InventoryTestCase):
    def test_receipt_create_creates_positive_movement(self):
        self.client.force_login(self.worker)
        response = self.client.post(
            reverse('receipt_create'),
            {'material': self.material.pk, 'location': self.location.pk, 'quantity': '10', 'notes': ''},
        )
        self.assertRedirects(response, reverse('dashboard'))
        movement = StockMovement.objects.get()
        self.assertEqual(movement.movement_type, StockMovement.MovementType.RECEIPT)
        self.assertEqual(movement.quantity, Decimal('10'))
        self.assertEqual(movement.created_by, self.worker)


class ShipmentCreateTests(InventoryTestCase):
    def test_shipment_create_creates_negative_movement(self):
        self._movement(Decimal('10'))
        self.client.force_login(self.worker)
        response = self.client.post(
            reverse('shipment_create'),
            {'material': self.material.pk, 'location': self.location.pk, 'quantity': '4', 'notes': ''},
        )
        self.assertRedirects(response, reverse('dashboard'))
        shipment = StockMovement.objects.get(movement_type=StockMovement.MovementType.SHIPMENT)
        self.assertEqual(shipment.quantity, Decimal('-4'))

    def test_shipment_rejected_when_insufficient_stock(self):
        self._movement(Decimal('5'))
        self.client.force_login(self.worker)
        response = self.client.post(
            reverse('shipment_create'),
            {'material': self.material.pk, 'location': self.location.pk, 'quantity': '10', 'notes': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.MovementType.SHIPMENT).exists())


class AvailableQuantityServiceTests(InventoryTestCase):
    def test_get_available_quantity_sums_and_locks(self):
        self.assertEqual(get_available_quantity(self.material, self.location), Decimal('0'))
        self._movement(Decimal('10'))
        self._movement(Decimal('-3'), StockMovement.MovementType.SHIPMENT)
        self.assertEqual(get_available_quantity(self.material, self.location), Decimal('7'))
        with transaction.atomic():
            self.assertEqual(get_available_quantity(self.material, self.location, lock=True), Decimal('7'))


class AdjustmentCreateTests(InventoryTestCase):
    def test_worker_forbidden(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('adjustment_create'))
        self.assertEqual(response.status_code, 403)

    def test_manager_allowed(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('adjustment_create'))
        self.assertEqual(response.status_code, 200)

    def test_adjustment_increase_creates_positive_movement(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('adjustment_create'),
            {
                'material': self.material.pk,
                'location': self.location.pk,
                'direction': 'INCREASE',
                'quantity': '5',
                'notes': 'count correction',
            },
        )
        self.assertRedirects(response, reverse('dashboard'))
        movement = StockMovement.objects.get(movement_type=StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(movement.quantity, Decimal('5'))

    def test_adjustment_decrease_creates_negative_movement(self):
        self._movement(Decimal('10'))
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('adjustment_create'),
            {
                'material': self.material.pk,
                'location': self.location.pk,
                'direction': 'DECREASE',
                'quantity': '4',
                'notes': 'damaged',
            },
        )
        self.assertRedirects(response, reverse('dashboard'))
        movement = StockMovement.objects.get(movement_type=StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(movement.quantity, Decimal('-4'))

    def test_adjustment_decrease_rejected_when_insufficient_stock(self):
        self._movement(Decimal('5'))
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('adjustment_create'),
            {
                'material': self.material.pk,
                'location': self.location.pk,
                'direction': 'DECREASE',
                'quantity': '10',
                'notes': 'damaged',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.MovementType.ADJUSTMENT).exists())

    def test_adjustment_requires_notes(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('adjustment_create'),
            {
                'material': self.material.pk,
                'location': self.location.pk,
                'direction': 'INCREASE',
                'quantity': '5',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.MovementType.ADJUSTMENT).exists())
