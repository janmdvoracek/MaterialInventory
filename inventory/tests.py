from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from materials.models import Location, Material
from workorders.models import WorkOrder

from .models import StockMovement


class StockMovementTestCase(TestCase):
    def setUp(self):
        self.material = Material.objects.create(sku='SKU1', name='Steel Bar', unit_of_measure='pcs')
        self.location = Location.objects.create(name='Main Depot')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)

    def _line_item(self, quantity, movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE, **kwargs):
        return StockMovement.objects.create(
            material=self.material,
            location=self.location,
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
        self.assertEqual(consumed.get_movement_type_display(), 'Zpracování – spotřeba')
        self.assertEqual(produced.get_movement_type_display(), 'Zpracování – výroba')

    def test_only_the_two_transform_types_remain(self):
        # Receipt, shipment and adjustment went with stock tracking. Existing
        # rows may still carry those raw values, but nothing writes new ones.
        self.assertEqual(
            [value for value, _ in StockMovement.MovementType.choices],
            ['TRANSFORM_CONSUME', 'TRANSFORM_PRODUCE'],
        )

    def test_created_at_defaults_to_now(self):
        before = timezone.now()
        movement = self._line_item(Decimal('1'))
        self.assertGreaterEqual(movement.created_at, before)
        self.assertLessEqual(movement.created_at, timezone.now())

    def test_created_at_is_overridable_unlike_recorded_at(self):
        backdated = timezone.now() - timezone.timedelta(days=3)
        movement = self._line_item(Decimal('1'), created_at=backdated)
        self.assertEqual(movement.created_at, backdated)
        # recorded_at is auto_now_add, so it keeps a truthful entry timestamp.
        self.assertGreater(movement.recorded_at, backdated)

    def test_ordering_is_newest_first(self):
        older = self._line_item(Decimal('1'), created_at=timezone.now() - timezone.timedelta(hours=2))
        newer = self._line_item(Decimal('1'))
        self.assertEqual(list(StockMovement.objects.all()), [newer, older])


class StockMovementWorkOrderTests(StockMovementTestCase):
    def test_line_items_are_reachable_from_their_work_order(self):
        work_order = WorkOrder.objects.create(created_by=self.worker, description='Drcení')
        self._line_item(Decimal('-5'), StockMovement.MovementType.TRANSFORM_CONSUME, work_order=work_order)
        self._line_item(Decimal('3'), work_order=work_order)
        self.assertEqual(work_order.movements.count(), 2)

    def test_work_order_is_nullable_for_rows_predating_stock_removal(self):
        # Receipts and shipments had no work order, and those rows still exist.
        movement = self._line_item(Decimal('1'))
        self.assertIsNone(movement.work_order)
