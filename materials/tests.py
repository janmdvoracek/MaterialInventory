import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User

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


class SeedDataCommandTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _write_csv(self, name, text):
        path = self.tmp_path / name
        path.write_text(text)
        return str(path)

    def _run(self, materials='', locations='', users=''):
        call_command(
            'seed_data',
            materials_file=self._write_csv('materials.csv', materials),
            locations_file=self._write_csv('locations.csv', locations),
            users_file=self._write_csv('users.csv', users),
        )

    def test_seed_materials_creates_and_updates(self):
        self._run(materials='sku,name,unit_of_measure,category\nSKU1,Steel Bar,pcs,Raw\n')
        material = Material.objects.get(sku='SKU1')
        self.assertEqual(material.name, 'Steel Bar')

        self._run(materials='sku,name,unit_of_measure,category\nSKU1,Steel Bar Renamed,pcs,Raw\n')
        self.assertEqual(Material.objects.count(), 1)
        material.refresh_from_db()
        self.assertEqual(material.name, 'Steel Bar Renamed')

    def test_seed_locations_dedupes_on_rerun(self):
        self._run(locations='name\nMain Depot\n')
        self._run(locations='name\nMain Depot\n')
        self.assertEqual(Location.objects.filter(name='Main Depot').count(), 1)

    def test_seed_users_creates_with_correct_role_and_staff_flag(self):
        self._run(
            users=(
                'username,first_name,last_name,email,role\n'
                'worker1,W,One,w1@example.com,WORKER\n'
                'manager1,M,One,m1@example.com,MANAGER\n'
                'admin1,A,One,a1@example.com,ADMIN\n'
            )
        )
        worker = User.objects.get(username='worker1')
        manager = User.objects.get(username='manager1')
        admin = User.objects.get(username='admin1')
        self.assertEqual(worker.role, User.Role.WORKER)
        self.assertFalse(worker.is_staff)
        self.assertEqual(manager.role, User.Role.MANAGER)
        self.assertFalse(manager.is_staff)
        self.assertEqual(admin.role, User.Role.ADMIN)
        self.assertTrue(admin.is_staff)

    def test_seed_users_skips_existing_username(self):
        self._run(users='username,first_name,last_name,email,role\nworker1,W,One,w1@example.com,WORKER\n')
        original_password = User.objects.get(username='worker1').password
        self._run(users='username,first_name,last_name,email,role\nworker1,Changed,Name,w1@example.com,WORKER\n')
        self.assertEqual(User.objects.filter(username='worker1').count(), 1)
        worker = User.objects.get(username='worker1')
        self.assertEqual(worker.first_name, 'W')
        self.assertEqual(worker.password, original_password)

    def test_seed_users_invalid_role_raises_command_error(self):
        with self.assertRaises(CommandError):
            self._run(users='username,first_name,last_name,email,role\nbad1,B,One,b1@example.com,SUPERVISOR\n')

    def test_missing_csv_files_are_skipped_gracefully(self):
        call_command(
            'seed_data',
            materials_file=str(self.tmp_path / 'does-not-exist-materials.csv'),
            locations_file=str(self.tmp_path / 'does-not-exist-locations.csv'),
            users_file=str(self.tmp_path / 'does-not-exist-users.csv'),
        )
        self.assertEqual(Material.objects.count(), 0)
        self.assertEqual(Location.objects.count(), 0)
        self.assertEqual(User.objects.count(), 0)
