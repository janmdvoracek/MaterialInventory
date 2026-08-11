import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User

from .models import Location, Machine, Material


class MaterialModelTests(TestCase):
    def test_material_str(self):
        material = Material.objects.create(sku='SKU1', name='Steel Bar', unit_of_measure='pcs')
        self.assertEqual(str(material), 'Steel Bar (SKU1)')

    def test_material_track_stock_defaults_to_true(self):
        material = Material.objects.create(sku='SKU1', name='Steel Bar', unit_of_measure='pcs')
        self.assertTrue(material.track_stock)

    def test_material_sku_unique(self):
        Material.objects.create(sku='SKU1', name='Steel Bar', unit_of_measure='pcs')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Material.objects.create(sku='SKU1', name='Other', unit_of_measure='pcs')


class LocationModelTests(TestCase):
    def test_location_str(self):
        location = Location.objects.create(name='Main Depot')
        self.assertEqual(str(location), 'Main Depot')

    def test_location_name_unique(self):
        Location.objects.create(name='Main Depot')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Location.objects.create(name='Main Depot')


class MachineModelTests(TestCase):
    def test_machine_str(self):
        machine = Machine.objects.create(name='Crusher A')
        self.assertEqual(str(machine), 'Crusher A')

    def test_machine_total_hours_defaults_to_zero(self):
        machine = Machine.objects.create(name='Crusher A')
        self.assertEqual(machine.total_hours, Decimal('0'))

    def test_machine_hourly_rate_defaults_to_none(self):
        machine = Machine.objects.create(name='Crusher A')
        self.assertIsNone(machine.hourly_rate)

    def test_machine_name_unique(self):
        Machine.objects.create(name='Crusher A')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Machine.objects.create(name='Crusher A')


class SeedDataCommandTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _write_csv(self, name, text):
        path = self.tmp_path / name
        path.write_text(text)
        return str(path)

    def _run(self, materials='', locations='', machines='', users=''):
        call_command(
            'seed_data',
            materials_file=self._write_csv('materials.csv', materials),
            locations_file=self._write_csv('locations.csv', locations),
            machines_file=self._write_csv('machines.csv', machines),
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

    def test_seed_machines_dedupes_on_rerun(self):
        self._run(machines='name\nCrusher A\n')
        self._run(machines='name\nCrusher A\n')
        self.assertEqual(Machine.objects.filter(name='Crusher A').count(), 1)

    def test_seed_machines_does_not_touch_total_hours(self):
        machine = Machine.objects.create(name='Crusher A', total_hours=Decimal('12.5'))
        self._run(machines='name\nCrusher A\n')
        machine.refresh_from_db()
        self.assertEqual(machine.total_hours, Decimal('12.5'))

    def test_seed_materials_track_stock_column(self):
        self._run(
            materials=(
                'sku,name,unit_of_measure,category,track_stock\nRAW1,Zemina,t,Zdroj,false\nFIN1,0/20,t,Frakce,true\n'
            )
        )
        self.assertFalse(Material.objects.get(sku='RAW1').track_stock)
        self.assertTrue(Material.objects.get(sku='FIN1').track_stock)

    def test_seed_materials_track_stock_defaults_true_when_omitted(self):
        self._run(materials='sku,name,unit_of_measure,category\nSKU1,Steel Bar,pcs,Raw\n')
        self.assertTrue(Material.objects.get(sku='SKU1').track_stock)

    def test_seed_machines_hourly_rate_column(self):
        self._run(machines='name,hourly_rate\nWarrior,83\nKladivo,\n')
        self.assertEqual(Machine.objects.get(name='Warrior').hourly_rate, Decimal('83'))
        self.assertIsNone(Machine.objects.get(name='Kladivo').hourly_rate)

    def test_seed_machines_hourly_rate_updates_on_rerun(self):
        self._run(machines='name,hourly_rate\nWarrior,83\n')
        self._run(machines='name,hourly_rate\nWarrior,90\n')
        self.assertEqual(Machine.objects.get(name='Warrior').hourly_rate, Decimal('90'))

    def test_seed_machines_preserves_hourly_rate_when_column_absent(self):
        # A rate set by hand in the admin must survive a re-seed from a CSV
        # that doesn't carry the optional hourly_rate column.
        Machine.objects.create(name='Warrior', hourly_rate=Decimal('99'))
        self._run(machines='name\nWarrior\n')
        self.assertEqual(Machine.objects.get(name='Warrior').hourly_rate, Decimal('99'))

    def test_seed_machines_preserves_hourly_rate_when_cell_blank(self):
        Machine.objects.create(name='Warrior', hourly_rate=Decimal('99'))
        self._run(machines='name,hourly_rate\nWarrior,\n')
        self.assertEqual(Machine.objects.get(name='Warrior').hourly_rate, Decimal('99'))

    def test_seed_materials_preserves_track_stock_when_column_absent(self):
        Material.objects.create(sku='RAW1', name='Zemina', unit_of_measure='t', track_stock=False)
        self._run(materials='sku,name,unit_of_measure,category\nRAW1,Zemina,t,Zdroj\n')
        self.assertFalse(Material.objects.get(sku='RAW1').track_stock)

    def test_seed_materials_preserves_track_stock_when_cell_blank(self):
        Material.objects.create(sku='RAW1', name='Zemina', unit_of_measure='t', track_stock=False)
        self._run(materials='sku,name,unit_of_measure,category,track_stock\nRAW1,Zemina,t,Zdroj,\n')
        self.assertFalse(Material.objects.get(sku='RAW1').track_stock)

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
        self.assertFalse(worker.is_superuser)
        self.assertEqual(manager.role, User.Role.MANAGER)
        self.assertFalse(manager.is_staff)
        self.assertFalse(manager.is_superuser)
        self.assertEqual(admin.role, User.Role.ADMIN)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

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
            machines_file=str(self.tmp_path / 'does-not-exist-machines.csv'),
            users_file=str(self.tmp_path / 'does-not-exist-users.csv'),
        )
        self.assertEqual(Material.objects.count(), 0)
        self.assertEqual(Location.objects.count(), 0)
        self.assertEqual(Machine.objects.count(), 0)
        self.assertEqual(User.objects.count(), 0)
