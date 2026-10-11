"""Fill-ups: on the job form and on the Stroje fuel card."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from machines.models import Machine
from workorders.models import MachineRefuel, MachineUsage, StockMovement, WorkerHours, WorkOrder
from workorders.reports import FUEL_PAGE_PARAM, HISTORY_PAGE_SIZE

from .helpers import ReviewFixtureMixin, job_payload


class MachineRefuelTests(ReviewFixtureMixin, TestCase):
    """Fuel on the machines section.

    Only the machine is required; a fuel-only submission is a valid job, exempt from the mass balance and the work
    fields; `job_edit` merges usage and fuel back into one row per machine.
    """

    def _submit(self, machine_rows, url=None, **overrides):
        """A balanced job carrying `machine_rows`, unless a test empties the material sections."""
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=machine_rows,
        )
        payload.update(overrides)
        return self.client.post(url or reverse('transform_create'), payload)

    def _fuel_only(self, machine_rows, url=None, **overrides):
        """Just machines and litres: no materials, no motohodiny, no „Popis", no hours."""
        payload = job_payload([], [], machine_rows=machine_rows, description='', hours='')
        payload.update(overrides)
        return self.client.post(url or reverse('transform_create'), payload)

    def test_a_machine_row_records_litres_alongside_hours(self):
        self.client.force_login(self.worker)
        response = self._submit(
            [{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': Decimal('40.5')}]
        )
        self.assertRedirects(response, reverse('transform_create'))
        # One row in, two tables out: the usage and the fill-up are not derived
        # from each other and nothing checks one against the other.
        self.assertEqual(MachineUsage.objects.get().hours, Decimal('2.00'))
        refuel = MachineRefuel.objects.get()
        self.assertEqual(refuel.machine, self.machine_a)
        self.assertEqual(refuel.litres, Decimal('40.50'))

    def test_a_row_naming_only_litres_records_no_usage(self):
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a, 'litres': Decimal('30')}])
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineRefuel.objects.get().litres, Decimal('30.00'))
        # A machine that was only refuelled did not run: no hours to invent.
        self.assertFalse(MachineUsage.objects.exists())

    def test_zero_litres_beside_a_usage_is_the_blank_box(self):
        # A worker who types 0 for „didn't refuel" means what an empty box means,
        # and must not be told to type at least 0,01 instead.
        self.client.force_login(self.worker)
        response = self._submit(
            [{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': '0'}]
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineUsage.objects.count(), 1)
        # No fill-up of nothing: the row is a usage row and only that.
        self.assertFalse(MachineRefuel.objects.exists())

    def test_zero_litres_alone_is_a_machine_with_nothing_recorded(self):
        # Treated as blank all the way through, „stroj + 0" is a machine with
        # nothing beside it: a usage row, and no fill-up of nothing.
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a, 'litres': '0'}])
        self.assertRedirects(response, reverse('transform_create'))
        usage = MachineUsage.objects.get()
        self.assertIsNone(usage.hours)
        self.assertIsNone(usage.tons)
        self.assertFalse(MachineRefuel.objects.exists())

    def test_zero_litres_alone_is_not_a_fuel_only_job(self):
        # With no fill-up it records work, so the job is asked everything a
        # work job is — rather than being a fuel-only job that recorded nothing.
        self.client.force_login(self.worker)
        response = self._fuel_only([{'machine': self.machine_a, 'litres': '0'}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_the_litres_box_allows_zero_but_not_less(self):
        self.client.force_login(self.worker)
        self.assertContains(self.client.get(reverse('transform_create')), 'min="0"')
        response = self._submit(
            [{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': '-1'}]
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

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
        # As with litres: a worker who types 0 means „not recorded", and a stored
        # 0 would read on Stroje as a machine that ran and processed nothing.
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

    def test_hours_with_litres_and_no_tons_records_both(self):
        self.client.force_login(self.worker)
        response = self._submit([{'machine': self.machine_a, 'hours': Decimal('2'), 'litres': Decimal('30')}])
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineUsage.objects.get().hours, Decimal('2.00'))
        self.assertEqual(MachineRefuel.objects.get().litres, Decimal('30.00'))

    def test_litres_without_a_machine_is_refused(self):
        self.client.force_login(self.worker)
        response = self._submit([], **{'machines-0-litres': '30'})
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
        self.assertFalse(MachineRefuel.objects.exists())

    def test_a_machine_alone_beside_fill_ups_ends_the_fuel_only_exemption(self):
        # It is a usage row, so the job records work and must balance.
        self.client.force_login(self.worker)
        response = self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('60')}, {'machine': self.machine_b}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

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

    def test_the_job_pages_show_a_dash_for_unrecorded_hours(self):
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a}])
        WorkOrder.objects.filter(pk=work_order.pk).update(status=WorkOrder.Status.APPROVED)
        self.client.force_login(self.manager)
        for url in (reverse('job_detail', args=[work_order.pk]), reverse('machine_dashboard')):
            with self.subTest(url=url):
                content = self.client.get(url).content.decode()
                # The hours cell follows the machine's name in both tables.
                self.assertRegex(content, r'<td>Crusher A</td>\s*<td>—</td>')

    def test_a_job_of_nothing_but_fill_ups_is_valid(self):
        self.client.force_login(self.worker)
        response = self._fuel_only(
            [
                {'machine': self.machine_a, 'litres': Decimal('60')},
                {'machine': self.machine_b, 'litres': Decimal('45.5')},
            ]
        )
        self.assertRedirects(response, reverse('transform_create'))
        work_order = WorkOrder.objects.get()
        self.assertEqual(
            sorted((r.machine.name, r.litres) for r in work_order.machine_refuels.all()),
            [('Crusher A', Decimal('60.00')), ('Excavator B', Decimal('45.50'))],
        )
        # Nothing else is written — including no hours row for the author, whose
        # „Moje hodiny" could not have been answered (its minimum is half an hour).
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())
        self.assertEqual(work_order.description, '')
        # Still an ordinary job otherwise: dated, authored and awaiting review.
        self.assertEqual(work_order.created_by, self.worker)
        self.assertEqual(work_order.performed_on, timezone.localdate())
        self.assertEqual(work_order.status, WorkOrder.Status.PENDING)

    def test_a_fuel_only_job_needs_no_material_rows(self):
        """The mass balance is skipped rather than passed: there are no totals.

        Without the exemption „Přidejte alespoň jednu položku…" would refuse the
        submission, which is the whole reason `_is_fuel_only` exists.
        """
        self.client.force_login(self.worker)
        response = self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('20')}])
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineRefuel.objects.count(), 1)

    def test_a_job_that_also_records_work_still_needs_its_fields(self):
        # The exemption is only for a job that records nothing else. Add any
        # motohodiny and the row stops being a bare fill-up.
        self.client.force_login(self.worker)
        response = self._fuel_only(
            [{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': Decimal('20')}]
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Toto pole je třeba vyplnit.')
        self.assertContains(response, 'Přidejte alespoň jednu položku')

    def test_material_rows_without_a_description_are_still_refused(self):
        # Same rule from the other side: material rows are work, so the job's
        # own fields come back.
        self.client.force_login(self.worker)
        response = self._submit([], description='', hours='')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Toto pole je třeba vyplnit.')

    def test_a_fuel_only_job_may_still_carry_a_description(self):
        # Not asked for is not the same as refused — the four list pages read
        # that column, so a worker who types one keeps it.
        self.client.force_login(self.worker)
        response = self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('20')}], description='Tankování')
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkOrder.objects.get().description, 'Tankování')

    def test_the_same_machine_refuelled_twice_in_one_job_is_refused(self):
        # Same rule as the motohodiny: two rows for one machine are two halves
        # of a number that belongs on one row.
        self.client.force_login(self.worker)
        response = self._fuel_only(
            [
                {'machine': self.machine_a, 'litres': Decimal('20')},
                {'machine': self.machine_a, 'litres': Decimal('25')},
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MachineRefuel.objects.exists())
        self.assertContains(response, 'Sečtěte motohodiny, tuny a litry')

    def test_the_form_renders_the_litres_box_on_both_pages(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        for url in (reverse('transform_create'), reverse('job_edit', args=[work_order.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'name="machines-0-litres"')

    def test_edit_prefills_both_halves_on_one_row(self):
        work_order = self.submit_job(
            self.worker,
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': '40'}],
        )
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['machine_formset']
        self.assertEqual(
            formset.forms[0].initial,
            {'machine': self.machine_a.pk, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': Decimal('40')},
        )

    def test_edit_prefills_a_fill_up_that_has_no_usage_row(self):
        """A fuel-only job's row has litres and nothing else, and must come back
        that way — a row rendered blank would be saved as blank and the fill-up
        would vanish while the page reported success."""
        self.client.force_login(self.worker)
        self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('60')}])
        work_order = WorkOrder.objects.get()
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['machine_formset']
        self.assertEqual(formset.forms[0].initial, {'machine': self.machine_a.pk, 'litres': Decimal('60')})

    def test_editing_a_fuel_only_job_rewrites_its_litres(self):
        self.client.force_login(self.worker)
        self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('60')}])
        work_order = WorkOrder.objects.get()
        self.client.force_login(self.manager)
        response = self._fuel_only(
            [{'machine': self.machine_a, 'litres': Decimal('75')}],
            url=reverse('job_edit', args=[work_order.pk]),
        )
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))
        self.assertEqual(MachineRefuel.objects.get().litres, Decimal('75.00'))

    def test_edit_keeps_a_retired_machine_that_only_a_fill_up_names(self):
        """Same data-loss trap as the usage rows: a picker that excludes the
        stored machine renders the row empty and saving it drops the fill-up."""
        self.client.force_login(self.worker)
        self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('60')}])
        self.machine_a.is_active = False
        self.machine_a.save(update_fields=['is_active'])
        self.client.force_login(self.manager)
        work_order = WorkOrder.objects.get()
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['machine_formset']
        self.assertIn(self.machine_a, formset.forms[0].fields['machine'].queryset)

    def test_deleting_the_job_takes_its_fill_ups_with_it(self):
        self.client.force_login(self.worker)
        self._fuel_only([{'machine': self.machine_a, 'litres': Decimal('60')}])
        work_order = WorkOrder.objects.get()
        self.client.force_login(self.manager)
        self.client.post(reverse('job_delete', args=[work_order.pk]))
        self.assertFalse(MachineRefuel.objects.exists())

    def test_the_job_pages_show_the_fill_ups(self):
        work_order = self.submit_job(
            self.worker,
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5'), 'litres': '40'}],
        )
        self.client.force_login(self.manager)
        for name in ('job_detail', 'job_delete'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=[work_order.pk]))
                self.assertContains(response, 'Tankování')


class MachineRefuelReportTests(TestCase):
    """The „Tankování" card on Stroje: per-machine litres, the fill-ups, the CSV.

    It answers to the page's one filter card, counts only approved jobs, and is
    gated exactly like the rest of Stroje.
    """

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.other = Machine.objects.create(name='Excavator B')
        self.retired = Machine.objects.create(name='Old Loader', is_active=False)
        self.client.force_login(self.manager)

    def _refuel(self, litres, machine=None, performed_on=None, status=WorkOrder.Status.APPROVED):
        work_order = WorkOrder.objects.create(created_by=self.worker, description='job', status=status)
        if performed_on is not None:
            WorkOrder.objects.filter(pk=work_order.pk).update(performed_on=performed_on)
        return MachineRefuel.objects.create(work_order=work_order, machine=machine or self.machine, litres=litres)

    def _summary(self, **params):
        return {
            row.name: row for row in self.client.get(reverse('machine_dashboard'), params).context['refuel_machines']
        }

    def test_litres_are_summed_per_machine(self):
        self._refuel(Decimal('40'))
        self._refuel(Decimal('25.5'))
        self._refuel(Decimal('10'), machine=self.other)
        rows = self._summary()
        self.assertEqual(rows['Crusher A'].filtered_litres, Decimal('65.50'))
        self.assertEqual(rows['Crusher A'].filtered_refuels, 2)
        self.assertEqual(rows['Excavator B'].filtered_litres, Decimal('10.00'))

    def test_the_card_renders_the_czech_decimal_comma(self):
        self._refuel(Decimal('65.5'))
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, 'Tankování')
        self.assertContains(response, '65,50 l')

    def test_a_machine_never_refuelled_reads_a_real_zero(self):
        # Unlike the tonnage on the other card, litres are not nullable, so a
        # zero here means "not refuelled in range" and not "unknown".
        self._refuel(Decimal('40'))
        self.assertEqual(self._summary()['Excavator B'].filtered_litres, Decimal('0'))

    def test_retired_machines_are_left_out(self):
        self.assertNotIn('Old Loader', self._summary())

    def test_pending_jobs_are_invisible(self):
        self._refuel(Decimal('40'), status=WorkOrder.Status.PENDING)
        self.assertEqual(self._summary()['Crusher A'].filtered_litres, Decimal('0'))

    def test_the_date_filter_narrows_the_totals(self):
        # Dated by the job, like every other row type: `job_edit` rewrites the
        # rows, so their own insert time would drift to the day of a correction.
        self._refuel(Decimal('40'), performed_on=date.today() - timedelta(days=40))
        self._refuel(Decimal('15'))
        rows = self._summary(date_from=(date.today() - timedelta(days=7)).isoformat())
        self.assertEqual(rows['Crusher A'].filtered_litres, Decimal('15.00'))
        self.assertEqual(rows['Crusher A'].filtered_refuels, 1)

    def test_naming_a_machine_narrows_the_card_to_it(self):
        self._refuel(Decimal('40'))
        self.assertEqual(list(self._summary(machine=self.other.pk)), ['Excavator B'])

    def test_an_invalid_filter_shows_no_machines(self):
        self._refuel(Decimal('40'))
        response = self.client.get(reverse('machine_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(list(response.context['refuel_machines']), [])
        self.assertEqual(response.context['refuel_total_litres'], Decimal('0'))

    def test_the_total_follows_the_filter(self):
        self._refuel(Decimal('40'), performed_on=date.today() - timedelta(days=40))
        self._refuel(Decimal('15'))
        self._refuel(Decimal('5'), machine=self.other)
        response = self.client.get(
            reverse('machine_dashboard'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        self.assertEqual(response.context['refuel_total_litres'], Decimal('20.00'))

    def test_the_detail_table_lists_the_fill_ups(self):
        self._refuel(Decimal('40'))
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, 'Detail tankování')
        self.assertEqual(len(response.context['fuel_page']['page_obj'].object_list), 1)

    def test_each_detail_table_pages_on_its_own_parameter(self):
        """Stroje holds two paginated tables. One shared `page` would move both,
        so the fill-ups carry their own — and its links have to keep the other
        table's parameter and the filters."""
        for _ in range(HISTORY_PAGE_SIZE + 1):
            self._refuel(Decimal('1'))
        response = self.client.get(reverse('machine_dashboard'), {FUEL_PAGE_PARAM: '2', 'page': '1'})
        fuel_page = response.context['fuel_page']
        self.assertEqual(fuel_page['page_obj'].number, 2)
        self.assertEqual(fuel_page['page_param'], FUEL_PAGE_PARAM)
        # Its own parameter dropped, the other table's kept.
        self.assertNotIn(FUEL_PAGE_PARAM, fuel_page['querystring'])
        self.assertIn('page=1', fuel_page['querystring'])
        # And the usage table is unmoved by a fuel page link.
        self.assertEqual(response.context['page_obj'].number, 1)

    def test_a_date_preset_resets_both_tables_to_page_one(self):
        response = self.client.get(reverse('machine_dashboard'), {'page': '2', FUEL_PAGE_PARAM: '3'})
        for preset in response.context['date_presets']:
            with self.subTest(preset=preset['key']):
                self.assertNotIn('page=', preset['querystring'])

    def test_the_export_carries_the_summary_table(self):
        self._refuel(Decimal('40'))
        self._refuel(Decimal('25.5'))
        response = self.client.get(reverse('machine_refuel_export'))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(rows[0], ['Stroj', 'Počet tankování', 'Natankováno (l)'])
        self.assertIn(['Crusher A', '2', '65,50'], rows)
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="tankovani-{date.today().isoformat()}.csv"',
        )

    def test_the_export_follows_the_page_filter(self):
        self._refuel(Decimal('40'), performed_on=date.today() - timedelta(days=40))
        self._refuel(Decimal('15'))
        response = self.client.get(
            reverse('machine_refuel_export'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertIn(['Crusher A', '1', '15,00'], rows)

    def test_an_invalid_filter_exports_only_a_header(self):
        self._refuel(Decimal('40'))
        response = self.client.get(reverse('machine_refuel_export'), {'date_from': 'not-a-date'})
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(len(rows), 1)

    def test_a_worker_cannot_reach_the_export(self):
        # Gated like the page it hangs off; hiding the nav link is not access control.
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse('machine_refuel_export')).status_code, 403)

    def _detail_rows(self, **params):
        response = self.client.get(reverse('machine_refuel_detail_export'), params)
        return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))

    def test_the_detail_export_carries_the_fill_ups(self):
        self._refuel(Decimal('25.5'), performed_on=date(2026, 9, 1))
        rows = self._detail_rows()
        self.assertEqual(rows[0], ['Provedeno', 'Stroj', 'Natankováno (l)', 'Zakázka', 'Kým'])
        self.assertEqual(rows[1], ['2026-09-01', 'Crusher A', '25,50', 'job', 'worker'])

    def test_the_detail_export_is_a_bom_csv_named_for_the_day(self):
        response = self.client.get(reverse('machine_refuel_detail_export'))
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="detail-tankovani-{date.today().isoformat()}.csv"',
        )

    def test_the_detail_export_is_every_row_not_one_page(self):
        for _ in range(HISTORY_PAGE_SIZE + 1):
            self._refuel(Decimal('1'))
        self.assertEqual(len(self._detail_rows()), HISTORY_PAGE_SIZE + 2)

    def test_the_detail_export_follows_the_filter_and_hides_pending_jobs(self):
        self._refuel(Decimal('40'), performed_on=date.today() - timedelta(days=40))
        self._refuel(Decimal('15'))
        self._refuel(Decimal('7'), status=WorkOrder.Status.PENDING)
        self._refuel(Decimal('5'), machine=self.other)
        rows = self._detail_rows(date_from=(date.today() - timedelta(days=7)).isoformat(), machine=self.machine.pk)
        self.assertEqual([row[2] for row in rows[1:]], ['15,00'])

    def test_the_detail_export_leaves_a_missing_description_blank(self):
        refuel = self._refuel(Decimal('40'))
        WorkOrder.objects.filter(pk=refuel.work_order_id).update(description='')
        self.assertEqual(self._detail_rows()[1][3], '')

    def test_an_invalid_filter_exports_only_a_header_on_the_detail_too(self):
        self._refuel(Decimal('40'))
        self.assertEqual(len(self._detail_rows(date_from='not-a-date')), 1)

    def test_a_worker_cannot_reach_the_detail_export(self):
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse('machine_refuel_detail_export')).status_code, 403)

    def test_both_fuel_downloads_are_linked_from_the_page(self):
        response = self.client.get(reverse('machine_dashboard'), {'date_from': '2026-01-01'})
        for name in ('machine_refuel_export', 'machine_refuel_detail_export'):
            with self.subTest(name=name):
                self.assertContains(response, f'{reverse(name)}?date_from=2026-01-01')
