import csv
import secrets
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from machines.models import Machine
from materials.models import Material


class Command(BaseCommand):
    help = (
        'Seed the material catalog, machinery, and employee accounts from CSV files. '
        'Safe to re-run: materials/machines are matched and updated, existing users are left untouched.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--materials-file', default='seed_data/materials.csv')
        parser.add_argument('--machines-file', default='seed_data/machines.csv')
        parser.add_argument('--users-file', default='seed_data/users.csv')

    def handle(self, *args, **options):
        with transaction.atomic():
            self.seed_materials(Path(options['materials_file']))
            self.seed_machines(Path(options['machines_file']))
            self.seed_users(Path(options['users_file']))

    def _read_csv(self, path, required=()):
        if not path.exists():
            self.stdout.write(self.style.WARNING(f'{path} not found, skipping.'))
            return []
        with path.open(newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            # Read the header inside the `with`: `fieldnames` is lazy, and on an
            # empty file `list(reader)` never triggers it, so touching it
            # afterwards raises "I/O operation on closed file".
            fieldnames = reader.fieldnames
            rows = list(reader)
        if fieldnames is None:
            # Empty file: nothing to seed, and nothing wrong either. Only a file
            # that *has* a header can have the wrong one.
            return []
        # A header the command cannot read would otherwise skip every row and
        # report "0 created" — which on the deployment runbook reads as "the
        # catalog was already up to date" rather than "this file is wrong".
        missing = [column for column in required if column not in fieldnames]
        if missing:
            raise CommandError(f'{path} is missing required column(s): {", ".join(missing)}.')
        return rows

    @staticmethod
    def _cell(row, column, default=''):
        """One CSV cell as a stripped string.

        `csv.DictReader` fills the columns a *short row* never reached with
        `None`, not `''` — so `machines.csv` written as `Bagr,10` under a
        `name,hourly_rate,rate_per_ton` header hands back `rate_per_ton=None`
        and a bare `.strip()` raises `AttributeError`. Trailing commas are easy
        to leave out by hand, and a spreadsheet export drops them too, so treat
        an unreached column exactly like an empty one.
        """
        value = row.get(column, default)
        return (value if value is not None else default).strip()

    def seed_materials(self, path):
        rows = self._read_csv(path, required=('sku', 'name'))
        created = 0
        updated = 0
        for row in rows:
            sku = self._cell(row, 'sku')
            if not sku:
                continue
            defaults = {'name': self._cell(row, 'name')}
            _, was_created = Material.objects.update_or_create(sku=sku, defaults=defaults)
            created += was_created
            updated += not was_created
        self.stdout.write(self.style.SUCCESS(f'Materials: {created} created, {updated} updated.'))

    def seed_machines(self, path):
        rows = self._read_csv(path, required=('name',))
        created = 0
        updated = 0
        for row in rows:
            name = self._cell(row, 'name')
            if not name:
                continue
            # Only touch the rate columns when they carry a value, so re-seeding
            # never silently wipes a rate set by hand in the admin.
            defaults = {}
            for column in ('hourly_rate', 'rate_per_ton'):
                raw = self._cell(row, column)
                if raw:
                    defaults[column] = Decimal(raw)
            _, was_created = Machine.objects.update_or_create(name=name, defaults=defaults)
            created += was_created
            updated += not was_created
        self.stdout.write(self.style.SUCCESS(f'Machines: {created} created, {updated} updated.'))

    def seed_users(self, path):
        rows = self._read_csv(path, required=('username',))
        valid_roles = {choice for choice, _ in User.Role.choices}
        created_users = []
        for row in rows:
            username = self._cell(row, 'username')
            if not username:
                continue
            if User.objects.filter(username=username).exists():
                self.stdout.write(f'User "{username}" already exists, skipped.')
                continue
            role = self._cell(row, 'role', User.Role.WORKER).upper()
            if role not in valid_roles:
                raise CommandError(
                    f'Invalid role "{role}" for user "{username}". Must be one of {sorted(valid_roles)}.'
                )
            password = secrets.token_urlsafe(12)
            is_admin = role == User.Role.ADMIN
            User.objects.create_user(
                username=username,
                first_name=self._cell(row, 'first_name'),
                last_name=self._cell(row, 'last_name'),
                email=self._cell(row, 'email'),
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
