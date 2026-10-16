"""Shared fixtures for the workorders tests."""

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from locations.models import Location
from machines.models import FuelSource, Machine
from materials.models import Material
from workorders.models import WorkOrder


def default_location():
    """The location every job payload names, since a work job cannot be recorded without one."""
    return Location.objects.get_or_create(name='Lom Sever')[0]


def job_payload(
    consumed_rows,
    produced_rows,
    machine_rows=None,
    worker_rows=None,
    description='Test job',
    notes='',
    hours='1',
):
    """POST data for the Transform form, which the edit form re-uses verbatim."""
    machine_rows = machine_rows or []
    worker_rows = worker_rows or []
    data = {
        'description': description,
        'notes': notes,
        'hours': hours,
        # Pre-filled on the real form, so every browser posts it.
        'location': default_location().pk,
        'performed_on': timezone.localdate().isoformat(),
        'consumed-TOTAL_FORMS': str(max(len(consumed_rows), 1)),
        'consumed-INITIAL_FORMS': '0',
        'consumed-MIN_NUM_FORMS': '0',
        'consumed-MAX_NUM_FORMS': '1000',
        'produced-TOTAL_FORMS': str(max(len(produced_rows), 1)),
        'produced-INITIAL_FORMS': '0',
        'produced-MIN_NUM_FORMS': '0',
        'produced-MAX_NUM_FORMS': '1000',
        'machines-TOTAL_FORMS': str(max(len(machine_rows), 1)),
        'machines-INITIAL_FORMS': '0',
        'machines-MIN_NUM_FORMS': '0',
        'machines-MAX_NUM_FORMS': '1000',
        'workers-TOTAL_FORMS': str(max(len(worker_rows), 1)),
        'workers-INITIAL_FORMS': '0',
        'workers-MIN_NUM_FORMS': '0',
        'workers-MAX_NUM_FORMS': '1000',
    }
    for i, row in enumerate(worker_rows):
        data[f'workers-{i}-user'] = row['user'].pk
        data[f'workers-{i}-hours'] = str(row['hours'])
    for i, row in enumerate(consumed_rows):
        data[f'consumed-{i}-material'] = row['material'].pk
        data[f'consumed-{i}-quantity'] = str(row['quantity'])
    for i, row in enumerate(produced_rows):
        data[f'produced-{i}-material'] = row['material'].pk
        data[f'produced-{i}-quantity'] = str(row['quantity'])
    for i, row in enumerate(machine_rows):
        data[f'machines-{i}-machine'] = row['machine'].pk
        if 'hours' in row:
            data[f'machines-{i}-hours'] = str(row['hours'])
        # Tuny default in beside motohodiny, so most tests need only name the hours.
        if 'hours' in row or 'tons' in row:
            data[f'machines-{i}-tons'] = str(row.get('tons', '5'))
    return data


def refuel_payload(rows, notes='', performed_on=None):
    """POST data for the Tankování form (and its edit form). Each row: `machine`, `source`, `litres`."""
    data = {
        'notes': notes,
        'performed_on': (performed_on or timezone.localdate()).isoformat(),
        'refuels-TOTAL_FORMS': str(max(len(rows), 1)),
        'refuels-INITIAL_FORMS': '0',
        'refuels-MIN_NUM_FORMS': '0',
        'refuels-MAX_NUM_FORMS': '1000',
    }
    for i, row in enumerate(rows):
        for name in ('machine', 'source'):
            if row.get(name) is not None:
                data[f'refuels-{i}-{name}'] = row[name].pk
        if 'litres' in row:
            data[f'refuels-{i}-litres'] = str(row['litres'])
    return data


class ReviewFixtureMixin:
    """Catalog, people and a helper that records a job the way the app does."""

    def setUp(self):
        self.material_raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.material_finished = Material.objects.create(sku='FIN', name='Frakce 8/16')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.other_worker = User.objects.create_user(username='other', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine_a = Machine.objects.create(name='Crusher A')
        self.machine_b = Machine.objects.create(name='Excavator B')
        self.source_tank = FuelSource.objects.create(name='Nádrž dílna')
        self.source_station = FuelSource.objects.create(name='Čerpací stanice')

    def submit_job(self, user, quantity=Decimal('5'), machine_rows=None, worker_rows=None, hours='1', notes=''):
        """Record a job through the real form, as `user` would."""
        self.client.force_login(user)
        response = self.client.post(
            reverse('transform_create'),
            job_payload(
                [{'material': self.material_raw, 'quantity': quantity}],
                [{'material': self.material_finished, 'quantity': quantity}],
                machine_rows=machine_rows,
                worker_rows=worker_rows,
                hours=hours,
                notes=notes,
            ),
        )
        self.assertRedirects(response, reverse('transform_create'))
        return WorkOrder.objects.order_by('-pk').first()
