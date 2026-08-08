import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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

    def test_dashboard_excludes_zero_net_stock(self):
        self._movement(Decimal('5'), StockMovement.MovementType.RECEIPT)
        self._movement(Decimal('-5'), StockMovement.MovementType.SHIPMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(list(response.context['stock']), [])

    def test_dashboard_shows_negative_net_stock(self):
        # A negative balance means either an untracked material consumed without
        # a receipt, or a real data error. Either way it must stay visible.
        self._movement(Decimal('-7'), StockMovement.MovementType.ADJUSTMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('dashboard'))
        stock = list(response.context['stock'])
        self.assertEqual(len(stock), 1)
        self.assertEqual(stock[0]['quantity'], Decimal('-7'))


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

    def test_shipment_of_untracked_material_bypasses_stock_check(self):
        untracked = Material.objects.create(
            sku='RAW1', name='Zemina', unit_of_measure='t', track_stock=False
        )
        self.client.force_login(self.worker)
        response = self.client.post(
            reverse('shipment_create'),
            {'material': untracked.pk, 'location': self.location.pk, 'quantity': '500', 'notes': ''},
        )
        self.assertRedirects(response, reverse('dashboard'))
        shipment = StockMovement.objects.get(movement_type=StockMovement.MovementType.SHIPMENT)
        self.assertEqual(shipment.quantity, Decimal('-500'))


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

    def test_adjustment_decrease_of_untracked_material_bypasses_stock_check(self):
        untracked = Material.objects.create(
            sku='RAW1', name='Zemina', unit_of_measure='t', track_stock=False
        )
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('adjustment_create'),
            {
                'material': untracked.pk,
                'location': self.location.pk,
                'direction': 'DECREASE',
                'quantity': '500',
                'notes': 'correction',
            },
        )
        self.assertRedirects(response, reverse('dashboard'))
        movement = StockMovement.objects.get(movement_type=StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(movement.quantity, Decimal('-500'))


class MovementHistoryTests(InventoryTestCase):
    def test_history_requires_login(self):
        response = self.client.get(reverse('movement_history'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_history_lists_all_movements_by_default(self):
        self._movement(Decimal('10'), StockMovement.MovementType.RECEIPT)
        self._movement(Decimal('-3'), StockMovement.MovementType.SHIPMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'))
        self.assertEqual(len(response.context['page_obj'].object_list), 2)

    def test_history_filters_by_material(self):
        other_material = Material.objects.create(sku='SKU2', name='Copper Wire', unit_of_measure='m')
        self._movement(Decimal('10'))
        StockMovement.objects.create(
            material=other_material,
            location=self.location,
            quantity=Decimal('5'),
            movement_type=StockMovement.MovementType.RECEIPT,
            created_by=self.worker,
        )
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'), {'material': self.material.pk})
        movements = response.context['page_obj'].object_list
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].material, self.material)

    def test_history_filters_by_location(self):
        other_location = Location.objects.create(name='Overflow Yard')
        self._movement(Decimal('10'))
        StockMovement.objects.create(
            material=self.material,
            location=other_location,
            quantity=Decimal('5'),
            movement_type=StockMovement.MovementType.RECEIPT,
            created_by=self.worker,
        )
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'), {'location': other_location.pk})
        movements = response.context['page_obj'].object_list
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].location, other_location)

    def test_history_filters_by_movement_type(self):
        self._movement(Decimal('10'), StockMovement.MovementType.RECEIPT)
        self._movement(Decimal('-3'), StockMovement.MovementType.SHIPMENT)
        self._movement(Decimal('-1'), StockMovement.MovementType.ADJUSTMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'), {'movement_type': 'SHIPMENT'})
        movements = response.context['page_obj'].object_list
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].movement_type, StockMovement.MovementType.SHIPMENT)

    def test_history_filters_by_created_by(self):
        self._movement(Decimal('10'), user=self.worker)
        self._movement(Decimal('5'), user=self.manager)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'), {'created_by': self.manager.pk})
        movements = response.context['page_obj'].object_list
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].created_by, self.manager)

    def test_history_filters_by_date_range(self):
        old = self._movement(Decimal('10'))
        recent = self._movement(Decimal('5'))
        StockMovement.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=10))
        StockMovement.objects.filter(pk=recent.pk).update(created_at=timezone.now())
        self.client.force_login(self.worker)
        response = self.client.get(
            reverse('movement_history'), {'date_from': (date.today() - timedelta(days=1)).isoformat()}
        )
        movements = response.context['page_obj'].object_list
        self.assertEqual(list(movements), [recent])

    def test_history_filters_combined(self):
        other_material = Material.objects.create(sku='SKU2', name='Copper Wire', unit_of_measure='m')
        matching = self._movement(Decimal('10'), StockMovement.MovementType.RECEIPT)
        self._movement(Decimal('-3'), StockMovement.MovementType.SHIPMENT)
        StockMovement.objects.create(
            material=other_material,
            location=self.location,
            quantity=Decimal('5'),
            movement_type=StockMovement.MovementType.RECEIPT,
            created_by=self.worker,
        )
        self.client.force_login(self.worker)
        response = self.client.get(
            reverse('movement_history'),
            {'material': self.material.pk, 'movement_type': 'RECEIPT'},
        )
        movements = response.context['page_obj'].object_list
        self.assertEqual(list(movements), [matching])

    def test_history_invalid_date_range_rerenders_with_error(self):
        self.client.force_login(self.worker)
        response = self.client.get(
            reverse('movement_history'),
            {'date_from': date.today().isoformat(), 'date_to': (date.today() - timedelta(days=1)).isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())

    def test_history_shows_no_rows_when_filter_is_invalid(self):
        self._movement(Decimal('10'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'), {'movement_type': 'NOT_A_TYPE'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(len(response.context['page_obj'].object_list), 0)

    def test_history_unfiltered_still_shows_everything(self):
        # Guard the fix above: an unbound form must not be treated as invalid.
        self._movement(Decimal('10'))
        self._movement(Decimal('-3'), StockMovement.MovementType.SHIPMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'))
        self.assertEqual(len(response.context['page_obj'].object_list), 2)

    def test_history_pagination_default_page_size(self):
        for _ in range(60):
            self._movement(Decimal('1'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'))
        page_obj = response.context['page_obj']
        self.assertEqual(len(page_obj.object_list), 50)
        self.assertTrue(page_obj.has_next())

    def test_history_pagination_second_page(self):
        for _ in range(60):
            self._movement(Decimal('1'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'), {'page': '2'})
        page_obj = response.context['page_obj']
        self.assertEqual(len(page_obj.object_list), 10)

    def test_history_pagination_out_of_range_page_clamps(self):
        self._movement(Decimal('10'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'), {'page': '999'})
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.number, page_obj.paginator.num_pages)

    def test_history_ordering_most_recent_first(self):
        first = self._movement(Decimal('10'))
        StockMovement.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=1))
        second = self._movement(Decimal('5'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'))
        movements = list(response.context['page_obj'].object_list)
        self.assertEqual(movements, [second, first])

    def test_worker_can_view_history(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history'))
        self.assertEqual(response.status_code, 200)

    def test_manager_can_view_history(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('movement_history'))
        self.assertEqual(response.status_code, 200)


class MovementHistoryExportTests(InventoryTestCase):
    def test_export_requires_login(self):
        response = self.client.get(reverse('movement_history_export'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_export_content_type_is_csv(self):
        self._movement(Decimal('10'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history_export'))
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_export_content_disposition_filename(self):
        self._movement(Decimal('10'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history_export'))
        self.assertIn('attachment; filename="movement_history.csv"', response['Content-Disposition'])

    def test_export_header_row(self):
        self._movement(Decimal('10'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history_export'))
        content = b''.join(response.streaming_content).decode()
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(
            rows[0],
            ['Datum', 'SKU', 'Materiál', 'Lokalita', 'Typ', 'Množství', 'Jednotka', 'Zakázka', 'Vytvořil', 'Poznámka'],
        )

    def test_export_row_count_matches_filtered_queryset(self):
        for _ in range(60):
            self._movement(Decimal('1'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history_export'))
        content = b''.join(response.streaming_content).decode()
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(len(rows) - 1, 60)

    def test_export_respects_filters(self):
        self._movement(Decimal('10'), StockMovement.MovementType.RECEIPT)
        self._movement(Decimal('-3'), StockMovement.MovementType.SHIPMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history_export'), {'movement_type': 'SHIPMENT'})
        content = b''.join(response.streaming_content).decode()
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(len(rows) - 1, 1)
        self.assertEqual(rows[1][4], 'Výdej')

    def test_worker_can_export_csv(self):
        self._movement(Decimal('10'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history_export'))
        self.assertEqual(response.status_code, 200)

    def test_export_returns_nothing_when_filter_is_invalid(self):
        # An unusable filter must not fall through to exporting the whole ledger.
        self._movement(Decimal('10'))
        self._movement(Decimal('-3'), StockMovement.MovementType.SHIPMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history_export'), {'movement_type': 'NOT_A_TYPE'})
        content = b''.join(response.streaming_content).decode()
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(len(rows) - 1, 0)

    def test_export_escapes_formula_injection_in_notes(self):
        StockMovement.objects.create(
            material=self.material,
            location=self.location,
            quantity=Decimal('1'),
            movement_type=StockMovement.MovementType.RECEIPT,
            created_by=self.worker,
            notes='=1+1',
        )
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history_export'))
        content = b''.join(response.streaming_content).decode()
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(rows[1][9], "'=1+1")

    def test_export_leaves_negative_quantities_numeric(self):
        # Quantities must NOT be apostrophe-escaped or they stop being numbers.
        self._movement(Decimal('-4'), StockMovement.MovementType.SHIPMENT)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('movement_history_export'))
        content = b''.join(response.streaming_content).decode()
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(rows[1][5], '-4.000')
