from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from materials.models import Material
from workorders.models import WorkOrder

from .models import StockMovement


class StockMovementTestCase(TestCase):
    def setUp(self):
        self.material = Material.objects.create(sku='SKU1', name='Steel Bar')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        # Every line item belongs to a job — the column stopped being nullable
        # when the last job-less rows were deleted in inventory migration 0007.
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
        self.assertEqual(consumed.get_movement_type_display(), 'Zpracování – spotřeba')
        self.assertEqual(produced.get_movement_type_display(), 'Zpracování – výroba')

    def test_only_the_two_transform_types_remain(self):
        # Receipt, shipment and adjustment went with stock tracking, and the
        # last rows carrying those raw values went with migration 0007.
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
        # It was nullable only for the receipt/shipment rows that predated the
        # stock removal; migration 0007 deleted the last of them.
        self.assertFalse(StockMovement._meta.get_field('work_order').null)
