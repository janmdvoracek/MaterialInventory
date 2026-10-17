"""The CSV exports."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from machines.models import Machine
from materials.models import Material
from workorders.models import MachineUsage, StockMovement, WorkerHours, WorkOrder
from workorders.reports import HISTORY_PAGE_SIZE

from .helpers import default_location


class TableExportTests(TestCase):
    """The CSV downloads: Excel-ready under `cs` (BOM, `;`, comma decimals), exactly the filtered table, and gated
    like their page.
    """

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.other_worker = User.objects.create_user(username='other', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A', hourly_rate=Decimal('83'))
        self.raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.finished = Material.objects.create(sku='FIN', name='Frakce 8/16')

    def _job(
        self,
        *,
        performed_on=None,
        status=WorkOrder.Status.APPROVED,
        hours=None,
        usage=None,
        quantity=None,
        location=None,
    ):
        """One approved job carrying whichever of the three record types a test needs."""
        work_order = WorkOrder.objects.create(
            created_by=self.worker, description='job', status=status, location=location
        )
        if performed_on is not None:
            WorkOrder.objects.filter(pk=work_order.pk).update(performed_on=performed_on)
        for user, worked in hours or []:
            WorkerHours.objects.create(work_order=work_order, user=user, hours=worked)
        if usage is not None:
            machine_hours, tons = usage
            MachineUsage.objects.create(work_order=work_order, machine=self.machine, hours=machine_hours, tons=tons)
        if quantity is not None:
            StockMovement.objects.create(
                material=self.raw,
                quantity=-quantity,
                movement_type=StockMovement.MovementType.TRANSFORM_CONSUME,
                work_order=work_order,
                created_by=self.worker,
            )
            StockMovement.objects.create(
                material=self.finished,
                quantity=quantity,
                movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE,
                work_order=work_order,
                created_by=self.worker,
            )
        return work_order

    def _rows(self, response):
        """The downloaded file parsed the way Excel reads it — BOM dropped, split on `;`."""
        text = response.content.decode('utf-8-sig')
        return list(csv.reader(io.StringIO(text), delimiter=';'))

    def test_export_is_a_utf8_bom_csv_attachment(self):
        # The BOM is what makes Excel read the file as UTF-8 rather than showing
        # „Štěrk" as mojibake, and the date in the name keeps two exports of the
        # same report from colliding in one downloads folder.
        self.client.force_login(self.manager)
        response = self.client.get(reverse('material_dashboard_export'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="souhrn-materialu-{date.today().isoformat()}.csv"',
        )

    def test_numbers_use_the_czech_decimal_comma(self):
        # A dot would be read as a thousands separator (or as text) by an Excel
        # running under cs, so the file has to agree with what the page renders.
        self._job(usage=(Decimal('100'), None))
        self._job(usage=(Decimal('112.5'), Decimal('20.5')))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard_export'))
        self.assertIn('112,5;12,5;20,50;83,00', response.content.decode('utf-8-sig'))

    def test_machine_export_carries_the_summary_table(self):
        # Motohodiny are meter readings: the latest one, then the hours run.
        self._job(usage=(Decimal('100'), None))
        self._job(usage=(Decimal('112.5'), Decimal('20.5')))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_dashboard_export')))
        self.assertEqual(
            rows[0],
            [
                'Stroj',
                'Stav motohodin',
                'Hodin za období',
                'Tuny',
                'Sazba (Kč/hod)',
                'Cena (Kč/t)',
                'Cena za hodiny (Kč)',
                'Cena za tuny (Kč)',
                'Celkem (Kč)',
            ],
        )
        # The unset rate_per_ton is blank, not a dash: the page's „—" is text
        # that would break a column of numbers, and blank stays out of a SUM.
        # The machine is priced by the hour only, so „Celkem" is that side.
        # No thousands separator: `USE_THOUSAND_SEPARATOR` is off, and the
        # Czech one is a non-breaking space that Excel would not parse.
        self.assertEqual(rows[1], ['Crusher A', '112,5', '12,5', '20,50', '83,00', '', '1037,50', '', '1037,50'])

    def test_unknown_cost_exports_blank_rather_than_zero(self):
        # Billed per tonne with the tonnage unknown: „Celkem" is not computable,
        # and a 0 Kč in a spreadsheet column would be summed as a free machine.
        Machine.objects.filter(pk=self.machine.pk).update(rate_per_ton=Decimal('35.50'))
        self._job(usage=(Decimal('100'), None))
        self._job(usage=(Decimal('102'), None))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_dashboard_export')))
        self.assertEqual(rows[1][6:], ['166,00', '', ''])

    def test_unknown_tonnage_exports_blank_but_idle_hours_export_zero(self):
        # `tons` is nullable because rows predating the column mean *unknown*,
        # while a machine with no rows in range really did run zero hours — the
        # same distinction the page draws with a dash and a 0.
        self._job(usage=(Decimal('100'), None))
        self._job(usage=(Decimal('103'), None))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_dashboard_export')))
        self.assertEqual(rows[1][1:4], ['103,0', '3,0', ''])

    def test_a_first_reading_exports_a_blank_run_time(self):
        # One reading has nothing to subtract from: the hours run are unknown.
        self._job(usage=(Decimal('100'), None))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_dashboard_export')))
        self.assertEqual(rows[1][1:4], ['100,0', '', ''])

    def test_unrecorded_hours_export_blank(self):
        # A machine whose usage rows all left motohodiny blank: unknown, so a
        # blank cell that SUM skips rather than a 0 it would count.
        self._job(usage=(None, Decimal('5')))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_dashboard_export')))
        self.assertEqual(rows[1][1:4], ['', '', '5,00'])
        self.assertEqual(rows[1][6], '')

    def test_material_export_carries_the_summary_table(self):
        self._job(quantity=Decimal('12.5'))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_dashboard_export')))
        self.assertEqual(rows[0], ['Materiál', 'Spotřebováno (t)', 'Vyrobeno (t)', 'Rozdíl (t)'])
        self.assertEqual(rows[1], ['Frakce 8/16', '0,00', '12,50', '12,50'])
        # Consumed reads positive, like the page: the form asked for a positive
        # number even though the row is stored negative.
        self.assertEqual(rows[2], ['Štěrk', '12,50', '0,00', '-12,50'])

    def test_hours_export_carries_the_summary_table(self):
        self._job(hours=[(self.worker, Decimal('2.5'))])
        self._job(hours=[(self.worker, Decimal('1.5'))])
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('time_worked_export')))
        self.assertEqual(rows[0], ['Pracovník', 'Hodiny', 'Zakázek'])
        self.assertEqual(rows[1], ['worker', '4,0', '2'])

    def test_export_follows_the_filter_on_the_page(self):
        # The link carries the page's querystring, so the file has to be the
        # table that was on screen and not the unfiltered report.
        self._job(usage=(Decimal('4'), Decimal('10')), performed_on=date.today() - timedelta(days=40))
        self._job(usage=(Decimal('5.5'), Decimal('6')))
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse('machine_dashboard_export'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        # The money follows the filter too — it is the same rows, priced. The
        # reading before the range is still the baseline for the one inside it.
        self.assertEqual(
            self._rows(response)[1], ['Crusher A', '5,5', '1,5', '6,00', '83,00', '', '124,50', '', '124,50']
        )

    def test_unapproved_job_is_not_exported(self):
        # A pending job is invisible in every report; the download is one.
        self._job(usage=(Decimal('4'), Decimal('10')), status=WorkOrder.Status.PENDING)
        self.client.force_login(self.manager)
        self.assertEqual(self._rows(self.client.get(reverse('machine_dashboard_export')))[1][1:4], ['', '0,0', ''])

    def test_invalid_filter_exports_a_header_and_nothing_else(self):
        # Same rule as the page: an unusable filter must not fall through to
        # handing back the whole report under the heading of a filtered one.
        self._job(quantity=Decimal('5'))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_dashboard_export'), {'date_from': 'not-a-date'}))
        self.assertEqual(len(rows), 1)

    def test_hours_export_is_scoped_to_the_worker_downloading_it(self):
        # The one export a plain worker can reach, so it must be scoped exactly
        # like their Souhrn page — not the depot's hours.
        self._job(hours=[(self.worker, Decimal('2'))])
        self._job(hours=[(self.other_worker, Decimal('8'))])
        self.client.force_login(self.worker)
        rows = self._rows(self.client.get(reverse('time_worked_export')))
        self.assertEqual(rows[1:], [['worker', '2,0', '1']])

    def test_worker_cannot_reach_the_manager_only_exports(self):
        # Hiding the link is not access control; all three are gated like their
        # pages. The detail file is the most worth gating: it names every job's
        # author and description, not just totals.
        self.client.force_login(self.worker)
        for name in ('machine_dashboard_export', 'material_dashboard_export', 'material_detail_export'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_exports_require_login(self):
        for name in (
            'time_worked_export',
            'machine_dashboard_export',
            'material_dashboard_export',
            'material_detail_export',
            'machine_refuel_export',
            'machine_refuel_detail_export',
            'machine_usage_detail_export',
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn('/login/', response.url)

    def test_detail_export_carries_the_line_items(self):
        # „Detail položek" one row per line item, in the page's own order and
        # with the same columns — the file is the table, not a rearrangement.
        work_order = self._job(quantity=Decimal('12.5'), location=default_location())
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_detail_export')))
        self.assertEqual(rows[0], ['ID', 'Provedeno', 'Lokace', 'Materiál', 'Druh', 'Množství (t)', 'Zakázka', 'Kým'])
        today = date.today().isoformat()
        self.assertEqual(
            rows[1:],
            [
                [str(work_order.pk), today, 'Lom Sever', 'Frakce 8/16', 'Výroba', '12,50', 'job', 'worker'],
                [str(work_order.pk), today, 'Lom Sever', 'Štěrk', 'Spotřeba', '12,50', 'job', 'worker'],
            ],
        )
        self.assertEqual(work_order.movements.count(), 2)

    def test_detail_rows_name_their_job(self):
        # Two jobs can share a date, description and author; the ID tells their
        # rows apart in the file, and links to the job on the page.
        first, second = self._job(quantity=Decimal('3')), self._job(quantity=Decimal('3'))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_detail_export')))
        self.assertEqual(sorted({row[0] for row in rows[1:]}), sorted({str(first.pk), str(second.pk)}))
        page = self.client.get(reverse('material_dashboard'))
        self.assertContains(page, f'<a href="{reverse("job_detail", args=[first.pk])}">#{first.pk}</a>', count=2)

    def test_detail_export_is_every_row_not_one_page(self):
        # The page paginates at HISTORY_PAGE_SIZE; a file holding the first 50
        # of 60 rows under the heading of the whole filter would be worse than
        # no file at all.
        for _ in range(HISTORY_PAGE_SIZE):
            self._job(quantity=Decimal('1'))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_detail_export')))
        self.assertEqual(len(rows) - 1, HISTORY_PAGE_SIZE * 2)

    def test_detail_export_follows_the_filter_and_hides_pending_jobs(self):
        # Same two rules as every other download, and for the same reason: it
        # reads `MaterialReport.detail_rows`, as the page does.
        self._job(quantity=Decimal('4'), performed_on=date.today() - timedelta(days=40))
        self._job(quantity=Decimal('9'), status=WorkOrder.Status.PENDING)
        self._job(quantity=Decimal('1.5'))
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse('material_detail_export'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        quantities = [row[5] for row in self._rows(response)[1:]]
        self.assertEqual(quantities, ['1,50', '1,50'])

    def test_detail_export_follows_the_movement_type_filter(self):
        self._job(quantity=Decimal('3'))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('material_detail_export'), {'movement_type': 'TRANSFORM_CONSUME'})
        self.assertEqual([row[4] for row in self._rows(response)[1:]], ['Spotřeba'])
        # The summary file keeps both directions, like the page's summary.
        summary = self._rows(
            self.client.get(reverse('material_dashboard_export'), {'movement_type': 'TRANSFORM_CONSUME'})
        )
        self.assertIn(['Frakce 8/16', '0,00', '3,00', '3,00'], summary)

    def test_detail_export_leaves_a_missing_description_blank(self):
        # The page prints „—" there; a dash is text in a column meant for text
        # the reader filters on, and blank is the same statement without it.
        WorkOrder.objects.filter(pk=self._job(quantity=Decimal('2')).pk).update(description='')
        self.client.force_login(self.manager)
        self.assertEqual(self._rows(self.client.get(reverse('material_detail_export')))[1][6], '')

    def test_detail_export_leaves_a_missing_location_blank(self):
        # Jobs recorded before the column existed, and fuel-only ones, have none.
        self._job(quantity=Decimal('2'))
        self.client.force_login(self.manager)
        self.assertEqual(self._rows(self.client.get(reverse('material_detail_export')))[1][2], '')

    def test_detail_export_is_a_bom_csv_named_for_the_day(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('material_detail_export'))
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="detail-polozek-{date.today().isoformat()}.csv"',
        )

    def test_invalid_filter_exports_a_header_and_nothing_else_on_the_detail_too(self):
        self._job(quantity=Decimal('5'))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('material_detail_export'), {'date_from': 'not-a-date'}))
        self.assertEqual(len(rows), 1)

    def test_usage_detail_export_carries_the_usage_rows(self):
        # „Detail používání strojů": the run time per row, as the page computes
        # it, and a blank cell where the page prints „—" (first reading, no tons).
        self._job(usage=(Decimal('100'), None), performed_on=date(2026, 9, 1))
        self._job(usage=(Decimal('112.5'), Decimal('20.5')), performed_on=date(2026, 9, 2))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_usage_detail_export'))
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="detail-pouzivani-stroju-{date.today().isoformat()}.csv"',
        )
        rows = self._rows(response)
        self.assertEqual(rows[0], ['Provedeno', 'Stroj', 'Hodin na zakázce', 'Tuny', 'Zakázka', 'Kým'])
        self.assertEqual(
            rows[1:],
            [
                ['2026-09-02', 'Crusher A', '12,5', '20,50', 'job', 'worker'],
                ['2026-09-01', 'Crusher A', '', '', 'job', 'worker'],
            ],
        )

    def test_usage_detail_export_is_every_row_not_one_page(self):
        for _ in range(HISTORY_PAGE_SIZE + 1):
            self._job(usage=(None, Decimal('1')))
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_usage_detail_export')))
        self.assertEqual(len(rows) - 1, HISTORY_PAGE_SIZE + 1)

    def test_usage_detail_export_follows_the_filter_and_hides_pending_jobs(self):
        # The run time still counts from the unfiltered baseline, as on the page.
        self._job(usage=(Decimal('100'), Decimal('4')), performed_on=date.today() - timedelta(days=40))
        self._job(usage=(Decimal('130'), Decimal('9')), status=WorkOrder.Status.PENDING)
        self._job(usage=(Decimal('110'), Decimal('1.5')))
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse('machine_usage_detail_export'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        self.assertEqual([row[2:4] for row in self._rows(response)[1:]], [['10,0', '1,50']])

    def test_usage_detail_export_is_gated_and_header_only_on_an_invalid_filter(self):
        self._job(usage=(Decimal('100'), None))
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse('machine_usage_detail_export')).status_code, 403)
        self.client.force_login(self.manager)
        rows = self._rows(self.client.get(reverse('machine_usage_detail_export'), {'date_from': 'not-a-date'}))
        self.assertEqual(len(rows), 1)

    def test_usage_detail_download_is_linked_from_the_page(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'), {'date_from': '2026-01-01'})
        url = reverse('machine_usage_detail_export')
        self.assertContains(response, f'{url}?date_from=2026-01-01')

    def test_both_material_downloads_are_linked_from_the_page(self):
        # One under each table, each carrying the filter that was on screen.
        self.client.force_login(self.manager)
        response = self.client.get(reverse('material_dashboard'), {'date_from': '2026-01-01'})
        for name in ('material_dashboard_export', 'material_detail_export'):
            with self.subTest(name=name):
                self.assertContains(response, f'{reverse(name)}?date_from=2026-01-01')
