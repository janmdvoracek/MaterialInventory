"""The job form's machines section: a machine, its meter reading and its tonnage. Fuel is not recorded here."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from workorders.forms import MachineUsageForm
from workorders.models import MachineUsage, WorkOrder

from .helpers import ReviewFixtureMixin, job_payload


class MachineUsageRowTests(ReviewFixtureMixin, TestCase):
    """Only the machine is required; a blank or zero number is stored as unknown."""

    def _submit(self, machine_rows, url=None, **overrides):
        """A balanced job carrying `machine_rows`."""
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=machine_rows,
        )
        payload.update(overrides)
        return self.client.post(url or reverse('transform_create'), payload)

    def test_the_machine_row_has_no_litres_box(self):
        # Fuel is a refuelling entry of its own (`Refuelling`), never part of a job.
        self.assertNotIn('litres', MachineUsageForm.base_fields)
        self.client.force_login(self.worker)
        self.assertNotContains(self.client.get(reverse('transform_create')), 'litres')

    def test_hours_without_tons_is_a_usage_row(self):
        # Machines are priced outside the app for now, so neither number is
        # asked for; the one left blank is stored as unknown.
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': ''}])
        self.assertRedirects(response, reverse('transform_create'))
        usage = MachineUsage.objects.get()
        self.assertEqual(usage.hours, Decimal('2.00'))
        self.assertIsNone(usage.tons)

    def test_tons_without_hours_is_a_usage_row(self):
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a, 'hours': '', 'tons': Decimal('5')}])
        self.assertRedirects(response, reverse('transform_create'))
        usage = MachineUsage.objects.get()
        self.assertIsNone(usage.hours)
        self.assertEqual(usage.tons, Decimal('5.00'))

    def test_zero_hours_and_tons_are_the_blank_box(self):
        # A worker who types 0 means „not recorded", and a stored 0 would read
        # on Stroje as a machine that ran and processed nothing.
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a, 'hours': '0', 'tons': '0'}])
        self.assertRedirects(response, reverse('transform_create'))
        usage = MachineUsage.objects.get()
        self.assertIsNone(usage.hours)
        self.assertIsNone(usage.tons)

    def test_negative_hours_and_tons_are_refused(self):
        self.client.force_login(self.worker)
        for field in ('hours', 'tons'):
            with self.subTest(field=field):
                response = self._submit([{'machine': self.machine_a, field: '-1'}])
                self.assertEqual(response.status_code, 200)
                self.assertFalse(WorkOrder.objects.exists())

    def test_a_number_without_a_machine_is_refused(self):
        self.client.force_login(self.worker)
        response = self._submit([], **{'machines-0-tons': '30'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Vyberte stroj')

    def test_a_machine_with_nothing_beside_it_is_a_usage_row(self):
        # Naming the machine still says it ran on the job, with both numbers
        # unknown rather than zero.
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a}])
        self.assertRedirects(response, reverse('transform_create'))
        usage = MachineUsage.objects.get()
        self.assertEqual(usage.machine, self.machine_a)
        self.assertIsNone(usage.hours)
        self.assertIsNone(usage.tons)

    def test_a_job_of_machines_alone_still_needs_its_fields_and_balance(self):
        # There is no fuel-only exemption any more: every job records work.
        self.client.force_login(self.worker)
        payload = job_payload([], [], machine_rows=[{'machine': self.machine_a}], description='', hours='')
        response = self.client.post(reverse('transform_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Toto pole je třeba vyplnit.')
        self.assertContains(response, 'Přidejte alespoň jednu položku')

    def test_the_same_machine_twice_in_one_job_is_refused(self):
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a, 'hours': '2'}, {'machine': self.machine_a, 'hours': '3'}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Stroj „Crusher A“ je vybraný víckrát.')

    def test_edit_keeps_a_machine_recorded_with_no_numbers(self):
        """The row comes back with the machine selected and both boxes empty,
        and saving it as rendered keeps the usage row rather than dropping it."""
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a}])
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['machine_formset']
        self.assertEqual(formset.forms[0].initial, {'machine': self.machine_a.pk, 'hours': None, 'tons': None})
        response = self._submit([{'machine': self.machine_a}], url=reverse('job_edit', args=[work_order.pk]))
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))
        usage = MachineUsage.objects.get()
        self.assertIsNone(usage.hours)
        self.assertIsNone(usage.tons)

    def test_edit_prefills_the_reading_and_tonnage(self):
        work_order = self.submit_job(
            self.worker, machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}]
        )
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['machine_formset']
        self.assertEqual(
            formset.forms[0].initial, {'machine': self.machine_a.pk, 'hours': Decimal('2'), 'tons': Decimal('5')}
        )

    def test_the_job_pages_show_a_dash_for_unrecorded_hours(self):
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a}])
        WorkOrder.objects.filter(pk=work_order.pk).update(status=WorkOrder.Status.APPROVED)
        self.client.force_login(self.manager)
        for url in (reverse('job_detail', args=[work_order.pk]), reverse('machine_dashboard')):
            with self.subTest(url=url):
                content = self.client.get(url).content.decode()
                # The hours cell follows the machine's name in both tables.
                self.assertRegex(content, r'<td>Crusher A</td>\s*<td>—</td>')

    def test_the_job_pages_no_longer_show_fuel(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        for name in ('job_detail', 'job_delete'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=[work_order.pk]))
                # The nav's „Tankování" link stays; the job's own fuel card and count are gone.
                self.assertNotContains(response, '<h2>Tankování</h2>', html=False)
                self.assertNotContains(response, '<td>Tankování</td>', html=False)
