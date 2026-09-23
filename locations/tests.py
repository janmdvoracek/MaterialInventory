from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Location


class LocationModelTests(TestCase):
    def test_location_str(self):
        location = Location.objects.create(name='Lom Sever')
        self.assertEqual(str(location), 'Lom Sever')

    def test_location_is_active_by_default(self):
        self.assertTrue(Location.objects.create(name='Lom Sever').is_active)

    def test_location_name_unique(self):
        Location.objects.create(name='Lom Sever')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Location.objects.create(name='Lom Sever')
