import csv
import secrets
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from materials.models import Location, Machine, Material


class Command(BaseCommand):
    help = (
        'Seed the material catalog, depot locations, machinery, and employee accounts from CSV files. '
        'Safe to re-run: materials/locations/machines are matched and updated, existing users are left untouched.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--materials-file', default='seed_data/materials.csv')
        parser.add_argument('--locations-file', default='seed_data/locations.csv')
        parser.add_argument('--machines-file', default='seed_data/machines.csv')
        parser.add_argument('--users-file', default='seed_data/users.csv')

    def handle(self, *args, **options):
        with transaction.atomic():
            self.seed_materials(Path(options['materials_file']))
            self.seed_locations(Path(options['locations_file']))
            self.seed_machines(Path(options['machines_file']))
            self.seed_users(Path(options['users_file']))

    def _read_csv(self, path):
        if not path.exists():
            self.stdout.write(self.style.WARNING(f'{path} not found, skipping.'))
            return []
        with path.open(newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))

    def seed_materials(self, path):
        rows = self._read_csv(path)
        created = 0
        updated = 0
        for row in rows:
            sku = row['sku'].strip()
            if not sku:
                continue
            defaults = {
                'name': row['name'].strip(),
                'unit_of_measure': row['unit_of_measure'].strip(),
                'category': row.get('category', '').strip(),
            }
            _, was_created = Material.objects.update_or_create(sku=sku, defaults=defaults)
            created += was_created
            updated += not was_created
        self.stdout.write(self.style.SUCCESS(f'Materials: {created} created, {updated} updated.'))

    def seed_locations(self, path):
        rows = self._read_csv(path)
        created = 0
        for row in rows:
            name = row['name'].strip()
            if not name:
                continue
            _, was_created = Location.objects.get_or_create(name=name)
            created += was_created
        self.stdout.write(self.style.SUCCESS(f'Locations: {created} created, {len(rows) - created} already existed.'))

    def seed_machines(self, path):
        rows = self._read_csv(path)
        created = 0
        updated = 0
        for row in rows:
            name = row['name'].strip()
            if not name:
                continue
            # Only touch hourly_rate when the column carries a value, so re-seeding
            # never silently wipes a rate set by hand in the admin.
            defaults = {}
            hourly_rate_raw = row.get('hourly_rate', '').strip()
            if hourly_rate_raw:
                defaults['hourly_rate'] = Decimal(hourly_rate_raw)
            _, was_created = Machine.objects.update_or_create(name=name, defaults=defaults)
            created += was_created
            updated += not was_created
        self.stdout.write(self.style.SUCCESS(f'Machines: {created} created, {updated} updated.'))

    def seed_users(self, path):
        rows = self._read_csv(path)
        valid_roles = {choice for choice, _ in User.Role.choices}
        created_users = []
        for row in rows:
            username = row['username'].strip()
            if not username:
                continue
            if User.objects.filter(username=username).exists():
                self.stdout.write(f'User "{username}" already exists, skipped.')
                continue
            role = row.get('role', User.Role.WORKER).strip().upper()
            if role not in valid_roles:
                raise CommandError(
                    f'Invalid role "{role}" for user "{username}". Must be one of {sorted(valid_roles)}.'
                )
            password = secrets.token_urlsafe(12)
            is_admin = role == User.Role.ADMIN
            User.objects.create_user(
                username=username,
                first_name=row.get('first_name', '').strip(),
                last_name=row.get('last_name', '').strip(),
                email=row.get('email', '').strip(),
                role=role,
                password=password,
                is_staff=is_admin,
                # is_staff alone only grants login to /admin/; without is_superuser
                # (or per-model permissions) the index page shows "you don't have
                # permission to view or edit anything".
                is_superuser=is_admin,
            )
            created_users.append((username, password))

        if created_users:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Created {len(created_users)} user(s). Temporary passwords '
                    '(share securely, then have them change it in the admin):'
                )
            )
            for username, password in created_users:
                self.stdout.write(f'  {username}: {password}')
        else:
            self.stdout.write('No new users created.')
