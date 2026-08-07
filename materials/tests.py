from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Location, Material


class MaterialModelTests(TestCase):
    def test_material_str(self):
        material = Material.objects.create(sku='SKU1', name='Steel Bar', unit_of_measure='pcs')
        self.assertEqual(str(material), 'Steel Bar (SKU1)')

    def test_material_sku_unique(self):
        Material.objects.create(sku='SKU1', name='Steel Bar', unit_of_measure='pcs')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Material.objects.create(sku='SKU1', name='Other', unit_of_measure='pcs')


class LocationModelTests(TestCase):
    def test_location_str(self):
        location = Location.objects.create(name='Main Depot')
        self.assertEqual(str(location), 'Main Depot')

    def test_location_name_unique(self):
        Location.objects.create(name='Main Depot')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Location.objects.create(name='Main Depot')
