"""Refuellings: the Tankování form, their review, and the Stroje fuel card."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from machines.models import FuelSource, Machine
from workorders.forms import LITRES_MAX
from workorders.models import MachineRefuel, Refuelling, WorkOrder
from workorders.refuels import NO_ROWS_ERROR
from workorders.reports import FUEL_PAGE_PARAM, HISTORY_PAGE_SIZE, MY_JOBS_LIMIT
from workorders.views import FORM_ERRORS_MESSAGE

from .helpers import ReviewFixtureMixin, refuel_payload


class RefuelFixtureMixin(ReviewFixtureMixin):
    def _row(self, machine=None, source=None, litres='40'):
        return {'machine': machine or self.machine_a, 'source': source or self.source_tank, 'litres': litres}

    def _submit(self, rows, url=None, **overrides):
        payload = refuel_payload(rows)
        payload.update(overrides)
        return self.client.post(url or reverse('refuel_create'), payload)

    def record(self, user, rows=None, **overrides):
        """Record a refuelling through the real form, as `user` would."""
        self.client.force_login(user)
        response = self._submit(rows or [self._row()], **overrides)
        self.assertRedirects(response, reverse('refuel_create'))
        return Refuelling.objects.order_by('-pk').first()


class RefuellingFormTests(RefuelFixtureMixin, TestCase):
    """Notes, a date and one row per machine (stroj, zdroj, litry); every row is all three or blank."""

    def test_a_worker_records_a_refuelling_that_waits_for_review(self):
        refuelling = self.record(
            self.worker,
            [self._row(litres='60'), self._row(machine=self.machine_b, source=self.source_station, litres='45.5')],
            notes='Ranní tankování',
        )
        self.assertEqual(refuelling.created_by, self.worker)
        self.assertEqual(refuelling.status, Refuelling.Status.PENDING)
        self.assertIsNone(refuelling.reviewed_by)
        self.assertEqual(refuelling.notes, 'Ranní tankování')
        self.assertEqual(refuelling.performed_on, timezone.localdate())
        self.assertEqual(
            [(row.machine, row.source, row.litres) for row in refuelling.machine_refuels.all()],
            [
                (self.machine_a, self.source_tank, Decimal('60.00')),
                (self.machine_b, self.source_station, Decimal('45.50')),
            ],
        )
        # A refuelling is not a job, and writes none.
        self.assertFalse(WorkOrder.objects.exists())

    def test_a_managers_own_refuelling_is_approved_at_once(self):
        refuelling = self.record(self.manager)
        self.assertEqual(refuelling.status, Refuelling.Status.APPROVED)
        self.assertEqual(refuelling.reviewed_by, self.manager)
        self.assertIsNotNone(refuelling.reviewed_at)

    def test_notes_are_optional(self):
        self.assertEqual(self.record(self.worker).notes, '')

    def test_the_date_defaults_to_today_and_may_be_earlier(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('refuel_create'))
        self.assertContains(response, f'value="{timezone.localdate().isoformat()}"')
        earlier = timezone.localdate() - timedelta(days=3)
        refuelling = self.record(self.worker, performed_on=earlier.isoformat())
        self.assertEqual(refuelling.performed_on, earlier)

    def test_a_future_date_is_refused(self):
        self.client.force_login(self.worker)
        tomorrow = timezone.localdate() + timedelta(days=1)
        response = self._submit([self._row()], performed_on=tomorrow.isoformat())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Datum provedení nemůže být v budoucnosti.')
        self.assertFalse(Refuelling.objects.exists())

    def test_at_least_one_machine_is_needed(self):
        self.client.force_login(self.worker)
        response = self._submit([])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, NO_ROWS_ERROR)
        self.assertFalse(Refuelling.objects.exists())

    def test_a_row_missing_its_source_or_litres_is_refused(self):
        self.client.force_login(self.worker)
        for missing in ('source', 'litres'):
            with self.subTest(missing=missing):
                row = self._row()
                del row[missing]
                response = self._submit([row])
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Vyplňte stroj, zdroj i litry')
                # A half-filled row is not a missing required field (as on the job form).
                self.assertContains(response, FORM_ERRORS_MESSAGE)
                self.assertFalse(Refuelling.objects.exists())

    def test_litres_must_be_positive_and_capped(self):
        self.client.force_login(self.worker)
        for litres in ('0', '-1', str(LITRES_MAX + Decimal('0.01'))):
            with self.subTest(litres=litres):
                response = self._submit([self._row(litres=litres)])
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Refuelling.objects.exists())
        self.record(self.worker, [self._row(litres=str(LITRES_MAX))])
        self.assertEqual(MachineRefuel.objects.get().litres, LITRES_MAX)

    def test_the_same_machine_twice_is_refused(self):
        # Same rule as every row section; a second source is a second refuelling.
        self.client.force_login(self.worker)
        response = self._submit([self._row(), self._row(source=self.source_station)])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stroj „Crusher A“ je vybraný víckrát.')
        self.assertFalse(Refuelling.objects.exists())

    def test_one_source_may_feed_several_machines(self):
        refuelling = self.record(self.worker, [self._row(), self._row(machine=self.machine_b)])
        self.assertEqual(refuelling.machine_refuels.count(), 2)

    def test_retired_machines_and_sources_are_not_offered(self):
        self.machine_b.is_active = False
        self.machine_b.save(update_fields=['is_active'])
        self.source_station.is_active = False
        self.source_station.save(update_fields=['is_active'])
        self.client.force_login(self.worker)
        form = self.client.get(reverse('refuel_create')).context['refuel_formset'].forms[0]
        self.assertNotIn(self.machine_b, form.fields['machine'].queryset)
        self.assertNotIn(self.source_station, form.fields['source'].queryset)
        self.assertIn(self.source_tank, form.fields['source'].queryset)

    def test_the_row_buttons_rebuild_the_form_without_saving(self):
        self.client.force_login(self.worker)
        response = self._submit([self._row()], add_refuels='')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['refuel_formset'].forms), 2)
        self.assertFalse(Refuelling.objects.exists())
        response = self._submit([self._row(), self._row(machine=self.machine_b)], remove_refuels='')
        self.assertEqual(len(response.context['refuel_formset'].forms), 1)

    def test_the_page_offers_both_pickers_and_the_litres_box(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('refuel_create'))
        for name in ('refuels-0-machine', 'refuels-0-source', 'refuels-0-litres', 'refuels-__prefix__-source'):
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, '<option value="" selected>Zdroj</option>', html=False)
        self.assertContains(response, 'data-unique-field="machine"')
        for script in ('js/job_rows.js', 'js/searchable_select.js'):
            self.assertContains(response, script)
        # No photos on a refuelling.
        self.assertNotContains(response, 'js/photo_picker.js')

    def test_the_nav_links_the_form_for_workers(self):
        self.client.force_login(self.worker)
        self.assertContains(self.client.get(reverse('time_worked')), f'href="{reverse("refuel_create")}"')

    def test_managers_reach_the_form_from_the_refuelling_list(self):
        # A sixth nav entry doesn't fit a phone, so the manager's nav stays at five.
        self.client.force_login(self.manager)
        form_link = f'href="{reverse("refuel_create")}"'
        self.assertNotContains(self.client.get(reverse('time_worked')), form_link)
        self.assertContains(self.client.get(reverse('refuel_dashboard')), form_link)
        self.assertEqual(self.client.get(reverse('refuel_create')).status_code, 200)

    def test_a_worker_sees_their_recent_refuellings_and_their_status(self):
        for _ in range(MY_JOBS_LIMIT + 1):
            self.record(self.worker)
        self.record(self.other_worker)
        self.client.force_login(self.worker)
        mine = self.client.get(reverse('refuel_create')).context['my_refuellings']
        self.assertEqual(len(mine), MY_JOBS_LIMIT)
        self.assertTrue(all(refuelling.created_by == self.worker for refuelling in mine))
        self.assertEqual(mine[0].total_litres, Decimal('40.00'))

    def test_a_manager_gets_no_recent_list(self):
        self.record(self.manager)
        self.assertFalse(self.client.get(reverse('refuel_create')).context['my_refuellings'])


class RefuellingReviewTests(RefuelFixtureMixin, TestCase):
    """Approve, edit, delete — like a job, and gated like one."""

    def test_the_review_pages_are_manager_only(self):
        refuelling = self.record(self.worker)
        urls = [reverse('refuel_dashboard')] + [
            reverse(name, args=[refuelling.pk]) for name in ('refuel_detail', 'refuel_edit', 'refuel_delete')
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse('refuel_approve', args=[refuelling.pk])).status_code, 403)
        self.assertEqual(Refuelling.objects.get().status, Refuelling.Status.PENDING)

    def test_the_list_shows_every_refuelling_and_the_pending_count(self):
        self.record(self.worker)
        self.record(self.manager)
        response = self.client.get(reverse('refuel_dashboard'))
        self.assertEqual(len(response.context['page_obj']), 2)
        self.assertEqual(response.context['pending_count'], 1)
        self.assertContains(response, 'Crusher A')
        self.assertContains(response, '40,00 l')

    def test_the_list_filters_by_status(self):
        self.record(self.worker)
        self.record(self.manager)
        response = self.client.get(reverse('refuel_dashboard'), {'status': Refuelling.Status.PENDING})
        self.assertEqual([r.created_by for r in response.context['page_obj']], [self.worker])

    def test_prehled_points_to_pending_refuellings(self):
        self.record(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual(response.context['pending_refuel_count'], 1)
        self.assertContains(response, reverse('refuel_dashboard'))

    def test_the_detail_page_lists_the_machines(self):
        refuelling = self.record(self.worker, notes='Poznámka')
        self.client.force_login(self.manager)
        response = self.client.get(reverse('refuel_detail', args=[refuelling.pk]))
        for text in ('Crusher A', 'Nádrž dílna', '40,00 l', 'Poznámka', 'Schválit'):
            self.assertContains(response, text)

    def test_approving_counts_it(self):
        refuelling = self.record(self.worker)
        self.client.force_login(self.manager)
        response = self.client.post(reverse('refuel_approve', args=[refuelling.pk]))
        self.assertRedirects(response, reverse('refuel_detail', args=[refuelling.pk]))
        refuelling.refresh_from_db()
        self.assertEqual(refuelling.status, Refuelling.Status.APPROVED)
        self.assertEqual(refuelling.reviewed_by, self.manager)

    def test_edit_prefills_and_rewrites_without_approving(self):
        refuelling = self.record(self.worker, notes='před')
        self.client.force_login(self.manager)
        url = reverse('refuel_edit', args=[refuelling.pk])
        response = self.client.get(url)
        self.assertEqual(response.context['order_form'].initial['notes'], 'před')
        self.assertEqual(
            response.context['refuel_formset'].forms[0].initial,
            {'machine': self.machine_a.pk, 'source': self.source_tank.pk, 'litres': Decimal('40')},
        )
        response = self._submit(
            [self._row(machine=self.machine_b, source=self.source_station, litres='75')], url=url, notes='po'
        )
        self.assertRedirects(response, reverse('refuel_detail', args=[refuelling.pk]))
        refuelling.refresh_from_db()
        self.assertEqual(refuelling.notes, 'po')
        self.assertEqual(refuelling.status, Refuelling.Status.PENDING)
        # The rows stay the author's entry, rewritten whole.
        self.assertEqual(refuelling.created_by, self.worker)
        fill_up = MachineRefuel.objects.get()
        self.assertEqual((fill_up.machine, fill_up.source, fill_up.litres), (self.machine_b, self.source_station, 75))

    def test_edit_keeps_retired_machines_and_sources_the_entry_names(self):
        """Otherwise the row renders blank and saving it drops the fill-up."""
        refuelling = self.record(self.worker)
        Machine.objects.filter(pk=self.machine_a.pk).update(is_active=False)
        FuelSource.objects.filter(pk=self.source_tank.pk).update(is_active=False)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('refuel_edit', args=[refuelling.pk]))
        for form in (response.context['refuel_formset'].forms[0], response.context['refuel_formset'].empty_form):
            self.assertIn(self.machine_a, form.fields['machine'].queryset)
            self.assertIn(self.source_tank, form.fields['source'].queryset)

    def test_a_fill_up_from_before_sources_asks_for_one_on_edit(self):
        # Migrated fill-ups have no source; the form requires one.
        refuelling = self.record(self.worker)
        MachineRefuel.objects.update(source=None)
        self.client.force_login(self.manager)
        url = reverse('refuel_edit', args=[refuelling.pk])
        self.assertIsNone(self.client.get(url).context['refuel_formset'].forms[0].initial['source'])
        response = self.client.post(url, refuel_payload([{'machine': self.machine_a, 'litres': '40'}]))
        self.assertContains(response, 'Vyplňte stroj, zdroj i litry')

    def test_deleting_takes_the_fill_ups_with_it(self):
        refuelling = self.record(self.worker)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('refuel_delete', args=[refuelling.pk])), 'Crusher A')
        response = self.client.post(reverse('refuel_delete', args=[refuelling.pk]))
        self.assertRedirects(response, reverse('refuel_dashboard'))
        self.assertFalse(Refuelling.objects.exists())
        self.assertFalse(MachineRefuel.objects.exists())


class MachineRefuelReportTests(TestCase):
    """The „Tankování" card on Stroje: per-machine litres, the fill-ups, the CSV.

    It answers to the page's one filter card, counts only approved refuellings,
    and is gated exactly like the rest of Stroje.
    """

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.other = Machine.objects.create(name='Excavator B')
        self.retired = Machine.objects.create(name='Old Loader', is_active=False)
        self.source = FuelSource.objects.create(name='Nádrž dílna')
        self.client.force_login(self.manager)

    def _refuel(self, litres, machine=None, performed_on=None, status=Refuelling.Status.APPROVED, source=None):
        refuelling = Refuelling.objects.create(created_by=self.worker, status=status)
        if performed_on is not None:
            Refuelling.objects.filter(pk=refuelling.pk).update(performed_on=performed_on)
        return MachineRefuel.objects.create(
            refuelling=refuelling, machine=machine or self.machine, source=source or self.source, litres=litres
        )

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
        self.assertContains(response, '65,50 l')

    def test_a_machine_never_refuelled_reads_a_real_zero(self):
        # Unlike the tonnage on the other card, litres are not nullable, so a
        # zero here means "not refuelled in range" and not "unknown".
        self._refuel(Decimal('40'))
        self.assertEqual(self._summary()['Excavator B'].filtered_litres, Decimal('0'))

    def test_retired_machines_are_left_out(self):
        self.assertNotIn('Old Loader', self._summary())

    def test_pending_refuellings_are_invisible(self):
        self._refuel(Decimal('40'), status=Refuelling.Status.PENDING)
        self.assertEqual(self._summary()['Crusher A'].filtered_litres, Decimal('0'))

    def test_the_date_filter_narrows_the_totals(self):
        # Dated by the refuelling, whose rows an edit rewrites.
        self._refuel(Decimal('40'), performed_on=date.today() - timedelta(days=40))
        self._refuel(Decimal('15'))
        rows = self._summary(date_from=(date.today() - timedelta(days=7)).isoformat())
        self.assertEqual(rows['Crusher A'].filtered_litres, Decimal('15.00'))
        self.assertEqual(rows['Crusher A'].filtered_refuels, 1)

    def test_the_author_filter_reads_the_refuelling(self):
        self._refuel(Decimal('40'))
        rows = self._summary(created_by=self.manager.pk)
        self.assertEqual(rows['Crusher A'].filtered_litres, Decimal('0'))
        rows = self._summary(created_by=self.worker.pk)
        self.assertEqual(rows['Crusher A'].filtered_litres, Decimal('40.00'))

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

    def test_the_detail_table_lists_the_fill_ups_with_their_source(self):
        refuel = self._refuel(Decimal('40'))
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, 'Detail tankování')
        self.assertEqual(len(response.context['fuel_page']['page_obj'].object_list), 1)
        self.assertContains(response, '<td>Nádrž dílna</td>', html=False)
        self.assertContains(response, reverse('refuel_detail', args=[refuel.refuelling_id]))

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
        self.assertEqual(rows[0], ['Provedeno', 'Stroj', 'Zdroj', 'Natankováno (l)', 'Kým'])
        self.assertEqual(rows[1], ['2026-09-01', 'Crusher A', 'Nádrž dílna', '25,50', 'worker'])

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

    def test_the_detail_export_follows_the_filter_and_hides_pending_refuellings(self):
        self._refuel(Decimal('40'), performed_on=date.today() - timedelta(days=40))
        self._refuel(Decimal('15'))
        self._refuel(Decimal('7'), status=Refuelling.Status.PENDING)
        self._refuel(Decimal('5'), machine=self.other)
        rows = self._detail_rows(date_from=(date.today() - timedelta(days=7)).isoformat(), machine=self.machine.pk)
        self.assertEqual([row[3] for row in rows[1:]], ['15,00'])

    def test_the_detail_export_leaves_a_missing_source_blank(self):
        refuel = self._refuel(Decimal('40'))
        MachineRefuel.objects.filter(pk=refuel.pk).update(source=None)
        self.assertEqual(self._detail_rows()[1][2], '')

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
