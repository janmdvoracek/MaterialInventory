from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import FuelSource, Machine


class MachineModelTests(TestCase):
    def test_machine_str(self):
        machine = Machine.objects.create(name='Crusher A')
        self.assertEqual(str(machine), 'Crusher A')

    def test_machine_hourly_rate_defaults_to_none(self):
        machine = Machine.objects.create(name='Crusher A')
        self.assertIsNone(machine.hourly_rate)

    def test_machine_rate_per_ton_defaults_to_none(self):
        machine = Machine.objects.create(name='Crusher A')
        self.assertIsNone(machine.rate_per_ton)

    def test_machine_name_unique(self):
        Machine.objects.create(name='Crusher A')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Machine.objects.create(name='Crusher A')


class FuelSourceModelTests(TestCase):
    def test_fuel_source_str(self):
        self.assertEqual(str(FuelSource.objects.create(name='Nádrž dílna')), 'Nádrž dílna')

    def test_fuel_source_is_active_by_default(self):
        self.assertTrue(FuelSource.objects.create(name='Nádrž dílna').is_active)

    def test_fuel_source_name_unique(self):
        FuelSource.objects.create(name='Nádrž dílna')
        with self.assertRaises(IntegrityError), transaction.atomic():
            FuelSource.objects.create(name='Nádrž dílna')
