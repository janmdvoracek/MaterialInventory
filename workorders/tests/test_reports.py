"""Souhrn, Stroje and Materiál, and the filters above them."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from machines.models import Machine
from materials.models import Material
from workorders.models import MachineRefuel, MachineUsage, Refuelling, StockMovement, WorkerHours, WorkOrder
from workorders.reports import MORE_ROWS, PREVIEW_ROWS, _last_month

from .helpers import ReviewFixtureMixin, job_payload


class ReportsUsePerformedOnTests(TestCase):
    """Every report keys off `performed_on`.

    Fixtures date the work 40 days before it was typed in, so it must land in last month's figures, not this week's.
    """

    def setUp(self):
        self.material_raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.material_finished = Material.objects.create(sku='FIN', name='Frakce 8/16')
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.long_ago = timezone.localdate() - timedelta(days=40)

    def _back_dated_job(self):
        """A job recorded today for work done 40 days ago, approved."""
        self.client.force_login(self.manager)
        self.client.post(
            reverse('transform_create'),
            job_payload(
                [{'material': self.material_raw, 'quantity': Decimal('5')}],
                [{'material': self.material_finished, 'quantity': Decimal('5')}],
                machine_rows=[{'machine': self.machine, 'hours': Decimal('3'), 'tons': '7'}],
                hours='4',
            )
            | {'performed_on': self.long_ago.isoformat()},
        )
        work_order = WorkOrder.objects.get()
        # A manager's own submission is approved on the spot, so it is already
        # reportable; the point here is the date, not the review.
        self.assertTrue(work_order.is_approved)
        self.assertEqual(work_order.created_at.date(), timezone.localdate())
        return work_order

    def _last_week(self):
        return {'date_from': (timezone.localdate() - timedelta(days=6)).isoformat()}

    def _that_month(self):
        return {
            'date_from': (self.long_ago - timedelta(days=1)).isoformat(),
            'date_to': (self.long_ago + timedelta(days=1)).isoformat(),
        }

    def test_hours_land_in_the_period_the_work_was_done(self):
        self._back_dated_job()
        response = self.client.get(reverse('time_worked'), self._that_month())
        self.assertEqual(response.context['summary'][0]['hours'], Decimal('4.00'))

    def test_hours_stay_out_of_the_week_it_was_typed_in(self):
        self._back_dated_job()
        response = self.client.get(reverse('time_worked'), self._last_week())
        self.assertEqual(response.context['summary'], [])

    def test_machine_totals_follow_the_work(self):
        self._back_dated_job()
        response = self.client.get(reverse('machine_dashboard'), self._that_month())
        machine = response.context['machines'][0]
        self.assertEqual(machine.filtered_reading, Decimal('3.00'))
        self.assertEqual(machine.filtered_tons, Decimal('7.00'))

    def test_machine_totals_stay_out_of_the_week_it_was_typed_in(self):
        self._back_dated_job()
        response = self.client.get(reverse('machine_dashboard'), self._last_week())
        machine = response.context['machines'][0]
        # No usage in range is a real 0 h, unlike usage that left motohodiny blank.
        self.assertEqual(machine.filtered_hours, Decimal('0'))
        self.assertEqual(len(response.context['rows']), 0)

    def test_material_totals_follow_the_work(self):
        self._back_dated_job()
        response = self.client.get(reverse('material_dashboard'), self._that_month())
        produced = next(row for row in response.context['materials'] if row == self.material_finished)
        self.assertEqual(produced.filtered_produced, Decimal('5.000'))

    def test_material_totals_stay_out_of_the_week_it_was_typed_in(self):
        self._back_dated_job()
        response = self.client.get(reverse('material_dashboard'), self._last_week())
        produced = next(row for row in response.context['materials'] if row == self.material_finished)
        self.assertIsNone(produced.filtered_produced)
        self.assertEqual(len(response.context['rows']), 0)

    def test_the_line_item_is_dated_by_its_job(self):
        # A StockMovement has no timestamp of its own at all — its date is its
        # job's, which is the date the page has to show.
        self._back_dated_job()
        response = self.client.get(reverse('material_dashboard'))
        self.assertContains(response, self.long_ago.isoformat())

    def test_the_usage_row_is_dated_by_its_job(self):
        # MachineUsage.created_at is when the row was written, which job_edit
        # rewrites; the page shows the job's date instead.
        self._back_dated_job()
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, self.long_ago.isoformat())

    def test_the_review_dashboard_filters_and_dates_by_the_work(self):
        work_order = self._back_dated_job()
        self.assertEqual(
            list(self.client.get(reverse('job_dashboard'), self._that_month()).context['rows']),
            [work_order],
        )
        self.assertEqual(
            list(self.client.get(reverse('job_dashboard'), self._last_week()).context['rows']),
            [],
        )
        self.assertContains(self.client.get(reverse('job_dashboard')), self.long_ago.isoformat())

    def test_jobs_are_ordered_by_when_the_work_was_done(self):
        older = self._back_dated_job()
        self.client.post(
            reverse('transform_create'),
            job_payload(
                [{'material': self.material_raw, 'quantity': Decimal('5')}],
                [{'material': self.material_finished, 'quantity': Decimal('5')}],
            ),
        )
        newer = WorkOrder.objects.exclude(pk=older.pk).get()
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual(list(response.context['rows']), [newer, older])


class MachineDashboardTests(TestCase):
    def setUp(self):
        # The page is manager/admin-only, so a manager reads it — but the jobs
        # behind the numbers are still a worker's.
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine_active = Machine.objects.create(name='Crusher A')
        self.machine_retired = Machine.objects.create(name='Old Excavator', is_active=False)
        self.client.force_login(self.manager)

    def _usage(self, hours, tons=None, status=WorkOrder.Status.APPROVED):
        work_order = WorkOrder.objects.create(created_by=self.worker, description='job', status=status)
        return MachineUsage.objects.create(work_order=work_order, machine=self.machine_active, hours=hours, tons=tons)

    def test_dashboard_lists_only_active_machines(self):
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(list(response.context['machines']), [self.machine_active])

    def test_dashboard_lists_every_machine_when_nothing_is_filtered(self):
        # An idle machine is still part of the fleet; it reads 0 h rather than
        # dropping off the table.
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(list(response.context['machines']), [self.machine_active])
        self.assertContains(response, '0,0 h')

    def test_totals_follow_the_date_filter(self):
        # Back-dated on the job, not on the usage row: the row's own
        # created_at is when it was written, and the page dates it by its job.
        old = self._usage(Decimal('4'), tons=Decimal('10'))
        WorkOrder.objects.filter(pk=old.work_order_id).update(performed_on=date.today() - timedelta(days=40))
        self._usage(Decimal('5.5'), tons=Decimal('6'))
        response = self.client.get(
            reverse('machine_dashboard'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        machine = response.context['machines'][0]
        self.assertEqual(machine.filtered_hours, Decimal('1.50'))
        self.assertEqual(machine.filtered_tons, Decimal('6.00'))

    def test_naming_a_machine_narrows_the_table_to_it(self):
        other = Machine.objects.create(name='Excavator B')
        response = self.client.get(reverse('machine_dashboard'), {'machine': other.pk})
        self.assertEqual(list(response.context['machines']), [other])

    def test_an_invalid_filter_shows_no_machines(self):
        # A fleet of zeros under a "these are your filtered results" heading
        # would read as an answer; it is not one.
        self._usage(Decimal('3'))
        response = self.client.get(reverse('machine_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(list(response.context['machines']), [])

    def test_dashboard_shows_approved_hours(self):
        self._usage(Decimal('100'))
        self._usage(Decimal('112.5'))
        response = self.client.get(reverse('machine_dashboard'))
        # Comma decimal separator: template output is localised under cs.
        self.assertContains(response, '12,5 h')

    def test_dashboard_shows_approved_tons(self):
        self._usage(Decimal('3'), tons=Decimal('12.5'))
        self._usage(Decimal('2'), tons=Decimal('8'))
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(response.context['machines'][0].filtered_tons, Decimal('20.50'))
        self.assertContains(response, '20,50')

    def test_dashboard_ignores_tons_from_unapproved_jobs(self):
        self._usage(Decimal('3'), tons=Decimal('12.5'), status=WorkOrder.Status.PENDING)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertIsNone(response.context['machines'][0].filtered_tons)

    def test_dashboard_shows_a_dash_when_no_tonnage_was_recorded(self):
        # `tons` is nullable because rows predating the column have no answer —
        # unknown, not zero. Sum skips them, and the page must not read that
        # back as a machine that processed nothing.
        self._usage(Decimal('3'))
        response = self.client.get(reverse('machine_dashboard'))
        self.assertIsNone(response.context['machines'][0].filtered_tons)
        self.assertEqual(response.context['machines'][0].filtered_reading, Decimal('3.00'))

    def test_dashboard_ignores_hours_from_unapproved_jobs(self):
        # A job nobody has signed off must not move the number a manager reads
        # off this screen.
        self._usage(Decimal('4'), status=WorkOrder.Status.PENDING)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '0,0 h')

    def test_dashboard_shows_zero_for_machine_without_usage(self):
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '0,0 h')

    def test_dashboard_shows_both_rates(self):
        self.machine_active.hourly_rate = Decimal('83')
        self.machine_active.rate_per_ton = Decimal('35.50')
        self.machine_active.save()
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '83,00')
        self.assertContains(response, '35,50')

    def test_dashboard_shows_dash_for_unset_rates(self):
        # Both rates are optional, so an unpriced machine must still render a
        # row. Seven dashes: an unused machine has no meter reading and no
        # tonnage either, and the three money columns are unknown rather than
        # zero for a machine that is not priced at all.
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '—', count=7)

    def test_cost_columns_multiply_the_totals_by_the_rates(self):
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.rate_per_ton = Decimal('35.50')
        self.machine_active.save()
        self._usage(Decimal('100'))
        self._usage(Decimal('102.5'), tons=Decimal('10'))
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('200.00'))
        self.assertEqual(machine.filtered_tons_cost, Decimal('355.00'))
        self.assertEqual(machine.filtered_total_cost, Decimal('555.00'))

    def test_cost_columns_follow_the_filter(self):
        # The money is the same data at a third zoom level: it has to be the
        # cost of the rows the page is showing, not of the whole ledger.
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        old = self._usage(Decimal('4'))
        WorkOrder.objects.filter(pk=old.work_order_id).update(performed_on=date.today() - timedelta(days=40))
        self._usage(Decimal('5.5'))
        response = self.client.get(
            reverse('machine_dashboard'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        self.assertEqual(response.context['machines'][0].filtered_hours_cost, Decimal('120.00'))

    def test_unpriced_side_is_unknown_and_drops_out_of_the_total(self):
        # An unset rate means "not priced that way", not "free" — so that side
        # is a dash, and the total is what the machine *is* priced on.
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        self._usage(Decimal('100'))
        self._usage(Decimal('102'), tons=Decimal('10'))
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('160.00'))
        self.assertIsNone(machine.filtered_tons_cost)
        self.assertEqual(machine.filtered_total_cost, Decimal('160.00'))

    def test_priced_machine_that_did_not_run_costs_zero_not_unknown(self):
        # The hours are a real zero, unlike a missing rate: a priced machine
        # that sat idle last week cost nothing, and that is an answer.
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('0'))
        self.assertEqual(machine.filtered_total_cost, Decimal('0'))

    def test_total_is_unknown_when_a_priced_side_is_unknown(self):
        # Billed per tonne, but the usage rows predate the `tons` column: the
        # real cost cannot be computed, and printing only the hours half under
        # „Celkem" would understate the bill.
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.rate_per_ton = Decimal('35.50')
        self.machine_active.save()
        self._usage(Decimal('100'), tons=None)
        self._usage(Decimal('102'), tons=None)
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('160.00'))
        self.assertIsNone(machine.filtered_tons_cost)
        self.assertIsNone(machine.filtered_total_cost)

    def test_hours_left_blank_on_every_usage_are_unknown_not_zero(self):
        # The machine ran — there are usage rows — but nobody recorded how long,
        # so it is a dash like an unknown tonnage, and the hours side of the bill
        # cannot be computed either.
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        self._usage(None, tons=Decimal('5'))
        response = self.client.get(reverse('machine_dashboard'))
        machine = response.context['machines'][0]
        self.assertIsNone(machine.filtered_hours)
        self.assertIsNone(machine.filtered_hours_cost)
        self.assertIsNone(machine.filtered_total_cost)
        self.assertNotContains(response, '0,0 h')

    def test_recorded_hours_still_sum_beside_unrecorded_ones(self):
        self._usage(Decimal('100'))
        self._usage(Decimal('103'))
        self._usage(None)
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours, Decimal('3.00'))

    def test_costs_ignore_unapproved_jobs(self):
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        self._usage(Decimal('4'), status=WorkOrder.Status.PENDING)
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours_cost, Decimal('0'))

    def test_costs_render_with_the_czech_comma(self):
        self.machine_active.hourly_rate = Decimal('80')
        self.machine_active.save()
        self._usage(Decimal('100'))
        self._usage(Decimal('102.5'))
        self.assertContains(self.client.get(reverse('machine_dashboard')), '200,00')


class MachineReadingTests(TestCase):
    """Motohodiny are the machine's hour-meter reading, not a duration.

    Stroje's summary shows the highest reading and prices the hours run; each
    detail row shows its reading minus the machine's previous approved one.
    """

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A', hourly_rate=Decimal('80'))
        self.client.force_login(self.manager)

    def _reading(self, hours, days_ago=0, machine=None, status=WorkOrder.Status.APPROVED):
        work_order = WorkOrder.objects.create(
            created_by=self.worker,
            description='job',
            status=status,
            performed_on=date.today() - timedelta(days=days_ago),
        )
        return MachineUsage.objects.create(work_order=work_order, machine=machine or self.machine, hours=hours)

    def _run_hours(self, response):
        return {usage.pk: usage.run_hours for usage in response.context['rows']}

    def test_summary_shows_the_highest_reading_and_prices_the_hours_run(self):
        self._reading(Decimal('1000'))
        self._reading(Decimal('1008'))
        self._reading(Decimal('1012.5'))
        response = self.client.get(reverse('machine_dashboard'))
        machine = response.context['machines'][0]
        self.assertEqual(machine.filtered_reading, Decimal('1012.5'))
        self.assertEqual(machine.filtered_hours, Decimal('12.5'))
        self.assertEqual(machine.filtered_hours_cost, Decimal('1000.00'))
        self.assertContains(response, '1012,5')

    def test_each_row_shows_the_time_since_the_previous_reading(self):
        first = self._reading(Decimal('1000'))
        second = self._reading(Decimal('1008'))
        third = self._reading(Decimal('1012.5'))
        run = self._run_hours(self.client.get(reverse('machine_dashboard')))
        # The first reading has nothing to compare against.
        self.assertEqual(run, {first.pk: None, second.pk: Decimal('8'), third.pk: Decimal('4.5')})

    def test_a_late_submission_recalculates_its_neighbours(self):
        # The job at 1008 is typed in after the one at 1012.5: by value it sits
        # between them, so it splits the 12.5 h rather than going negative.
        self._reading(Decimal('1000'), days_ago=3)
        later = self._reading(Decimal('1012.5'), days_ago=1)
        late = self._reading(Decimal('1008'), days_ago=2)
        run = self._run_hours(self.client.get(reverse('machine_dashboard')))
        self.assertEqual(run[late.pk], Decimal('8'))
        self.assertEqual(run[later.pk], Decimal('4.5'))

    def test_the_baseline_may_lie_outside_the_date_range(self):
        self._reading(Decimal('1000'), days_ago=40)
        recent = self._reading(Decimal('1006'))
        response = self.client.get(
            reverse('machine_dashboard'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        self.assertEqual(self._run_hours(response), {recent.pk: Decimal('6')})
        machine = response.context['machines'][0]
        self.assertEqual(machine.filtered_reading, Decimal('1006'))
        self.assertEqual(machine.filtered_hours, Decimal('6'))

    def test_a_pending_reading_is_not_a_baseline(self):
        self._reading(Decimal('1000'))
        self._reading(Decimal('1005'), status=WorkOrder.Status.PENDING)
        approved = self._reading(Decimal('1008'))
        run = self._run_hours(self.client.get(reverse('machine_dashboard')))
        self.assertEqual(run[approved.pk], Decimal('8'))

    def test_a_repeated_reading_counts_once(self):
        self._reading(Decimal('1000'))
        self._reading(Decimal('1008'))
        self._reading(Decimal('1008'))
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_hours, Decimal('8'))

    def test_readings_of_other_machines_are_not_a_baseline(self):
        other = Machine.objects.create(name='Excavator B')
        self._reading(Decimal('1000'), machine=other)
        usage = self._reading(Decimal('1008'))
        run = self._run_hours(self.client.get(reverse('machine_dashboard')))
        self.assertIsNone(run[usage.pk])

    def test_a_blank_reading_has_no_run_time_and_is_no_baseline(self):
        self._reading(Decimal('1000'))
        blank = self._reading(None)
        after = self._reading(Decimal('1008'))
        run = self._run_hours(self.client.get(reverse('machine_dashboard')))
        self.assertIsNone(run[blank.pk])
        self.assertEqual(run[after.pk], Decimal('8'))

    def test_only_a_first_reading_leaves_the_hours_cost_unknown(self):
        self._reading(Decimal('1000'))
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertEqual(machine.filtered_reading, Decimal('1000'))
        self.assertIsNone(machine.filtered_hours)
        self.assertIsNone(machine.filtered_hours_cost)

    def test_a_machine_with_no_reading_in_range_shows_a_dash_not_zero(self):
        machine = self.client.get(reverse('machine_dashboard')).context['machines'][0]
        self.assertIsNone(machine.filtered_reading)
        self.assertEqual(machine.filtered_hours, Decimal('0'))

    def test_the_job_form_asks_for_the_meter_reading(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        self.assertRegex(
            response.content.decode(), r'<input(?=[^>]*name="machines-0-hours")[^>]*title="Stav motohodin"'
        )


class MachineUsageDetailTests(TestCase):
    """The usage rows at the bottom of Stroje — worker scoping and filtering.

    Same page as the totals above them (`machine_dashboard`); these tests are
    about the rows.
    """

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.other_machine = Machine.objects.create(name='Excavator B')

    def _usage(self, user, machine=None, hours=Decimal('1'), tons=None, status=WorkOrder.Status.APPROVED):
        # Approved by default: the page reports signed-off jobs only, so an
        # unapproved fixture would be invisible for reasons unrelated to the
        # scoping these tests are about.
        work_order = WorkOrder.objects.create(created_by=user, description='job', status=status)
        return MachineUsage.objects.create(
            work_order=work_order, machine=machine or self.machine, hours=hours, tons=tons
        )

    def test_page_requires_login(self):
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_worker_gets_403(self):
        # Stroje is manager/admin-only in the view, not merely absent from a
        # worker's nav: typing the URL is a 403, not a scoped-down page.
        self._usage(self.worker)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_detail_shows_tons(self):
        self._usage(self.worker, tons=Decimal('12.5'))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        # Comma decimal separator: template output is localised under cs.
        self.assertContains(response, '12,50')

    def test_detail_shows_dash_for_row_without_tons(self):
        # Rows written before the column existed have no tonnage; the table
        # must still render them.
        self._usage(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertContains(response, '—')

    def test_created_by_filter_available_to_manager(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertIn('created_by', response.context['form'].fields)

    def test_manager_sees_usage_from_all_users(self):
        self._usage(self.worker)
        self._usage(self.manager)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(len(response.context['rows']), 2)

    def test_detail_filters_by_machine(self):
        matching = self._usage(self.worker, machine=self.machine)
        self._usage(self.worker, machine=self.other_machine)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'), {'machine': self.machine.pk})
        usages = response.context['rows']
        self.assertEqual(list(usages), [matching])

    def test_detail_shows_no_rows_when_filter_is_invalid(self):
        self._usage(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(len(response.context['rows']), 0)


class MaterialDashboardTests(TestCase):
    """Materiál: per-material tonnage and the line items behind it.

    Jobs are built directly, so rows can be back-dated or left pending without fighting the mass balance.
    """

    def setUp(self):
        # Manager/admin-only page, so a manager reads it — but the jobs behind
        # the numbers are a worker's.
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.raw = Material.objects.create(sku='RAW', name='Štěrk')
        self.finished = Material.objects.create(sku='FIN', name='Frakce 8/16')
        self.retired = Material.objects.create(sku='OLD', name='Vyřazený písek', is_active=False)
        self.client.force_login(self.manager)

    def _job(self, consumed=None, produced=None, status=WorkOrder.Status.APPROVED, performed_on=None):
        work_order = WorkOrder.objects.create(created_by=self.worker, description='job', status=status)
        if performed_on is not None:
            WorkOrder.objects.filter(pk=work_order.pk).update(performed_on=performed_on)
        for material, quantity in consumed or []:
            StockMovement.objects.create(
                material=material,
                # Consumed quantities are stored negative; the page has to show
                # back the positive number the form asked for.
                quantity=-quantity,
                movement_type=StockMovement.MovementType.TRANSFORM_CONSUME,
                work_order=work_order,
                created_by=self.worker,
            )
        for material, quantity in produced or []:
            StockMovement.objects.create(
                material=material,
                quantity=quantity,
                movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE,
                work_order=work_order,
                created_by=self.worker,
            )
        return work_order

    def _row(self, response, material):
        return next(row for row in response.context['materials'] if row == material)

    def test_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_worker_gets_403(self):
        # Keeping the nav entry out of a worker's menu is not access control.
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse('material_dashboard')).status_code, 403)

    def test_produced_tonnage_totals_across_jobs(self):
        # The motivating question: how much 8/16 did we make?
        self._job(consumed=[(self.raw, Decimal('5'))], produced=[(self.finished, Decimal('5'))])
        self._job(consumed=[(self.raw, Decimal('3'))], produced=[(self.finished, Decimal('3'))])
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(self._row(response, self.finished).filtered_produced, Decimal('8.000'))
        self.assertContains(response, '8,00 t')

    def test_consumed_tonnage_reads_positive(self):
        self._job(consumed=[(self.raw, Decimal('12.5'))], produced=[(self.finished, Decimal('12.5'))])
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(self._row(response, self.raw).filtered_consumed, Decimal('12.500'))
        # Comma decimal separator: template output is localised under cs.
        self.assertContains(response, '12,50 t')

    def test_net_is_produced_minus_consumed(self):
        # Sand crushed out of gravel on one job and fed back into another: the
        # net is what the two directions leave behind.
        self._job(consumed=[(self.raw, Decimal('10'))], produced=[(self.finished, Decimal('10'))])
        self._job(consumed=[(self.finished, Decimal('4'))], produced=[(self.raw, Decimal('4'))])
        response = self.client.get(reverse('material_dashboard'))
        finished = self._row(response, self.finished)
        self.assertEqual(finished.filtered_produced, Decimal('10.000'))
        self.assertEqual(finished.filtered_consumed, Decimal('4.000'))
        self.assertEqual(finished.filtered_net, Decimal('6.000'))
        self.assertEqual(self._row(response, self.raw).filtered_net, Decimal('-6.000'))

    def test_unapproved_job_counts_for_nothing(self):
        # A pending job is a proposal, not evidence — invisible in every report.
        self._job(
            consumed=[(self.raw, Decimal('5'))],
            produced=[(self.finished, Decimal('5'))],
            status=WorkOrder.Status.PENDING,
        )
        response = self.client.get(reverse('material_dashboard'))
        self.assertIsNone(self._row(response, self.finished).filtered_produced)
        self.assertEqual(self._row(response, self.finished).filtered_net, Decimal('0'))
        self.assertEqual(len(response.context['rows']), 0)

    def test_material_with_no_movements_still_appears(self):
        # "We made none last month" is an answer; a missing row is not.
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(list(response.context['materials']), [self.finished, self.raw])
        self.assertContains(response, '0,00 t')

    def test_naming_a_material_narrows_the_table_to_it(self):
        response = self.client.get(reverse('material_dashboard'), {'material': self.finished.pk})
        self.assertEqual(list(response.context['materials']), [self.finished])

    def test_retired_material_is_out_of_the_summary_but_still_filterable(self):
        self._job(consumed=[(self.retired, Decimal('2'))], produced=[(self.finished, Decimal('2'))])
        response = self.client.get(reverse('material_dashboard'))
        self.assertNotIn(self.retired, list(response.context['materials']))
        # Its history is still readable — that is what the filter is for.
        self.assertIn(self.retired, response.context['form'].fields['material'].queryset)
        narrowed = self.client.get(reverse('material_dashboard'), {'material': self.retired.pk})
        self.assertEqual(len(narrowed.context['rows']), 1)

    def test_totals_follow_the_date_filter(self):
        self._job(
            consumed=[(self.raw, Decimal('4'))],
            produced=[(self.finished, Decimal('4'))],
            performed_on=date.today() - timedelta(days=40),
        )
        self._job(consumed=[(self.raw, Decimal('1.5'))], produced=[(self.finished, Decimal('1.5'))])
        response = self.client.get(
            reverse('material_dashboard'), {'date_from': (date.today() - timedelta(days=7)).isoformat()}
        )
        self.assertEqual(self._row(response, self.finished).filtered_produced, Decimal('1.500'))
        self.assertEqual(len(response.context['rows']), 2)

    def test_an_invalid_filter_shows_nothing(self):
        # A catalog of zeros under a "these are your filtered results" heading
        # would read as an answer; it is not one.
        self._job(consumed=[(self.raw, Decimal('5'))], produced=[(self.finished, Decimal('5'))])
        response = self.client.get(reverse('material_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(list(response.context['materials']), [])
        self.assertEqual(len(response.context['rows']), 0)

    def test_detail_rows_name_the_direction_and_the_author(self):
        self._job(consumed=[(self.raw, Decimal('5'))], produced=[(self.finished, Decimal('5'))])
        response = self.client.get(reverse('material_dashboard'))
        self.assertEqual(len(response.context['rows']), 2)
        self.assertContains(response, 'Spotřeba')
        self.assertContains(response, 'Výroba')
        self.assertContains(response, self.worker.username)

    def test_the_movement_type_filter_narrows_the_detail_rows_only(self):
        # The summary already splits the two directions into columns; narrowing
        # it too would zero one column and turn „Rozdíl" into nonsense.
        self._job(consumed=[(self.raw, Decimal('5'))], produced=[(self.finished, Decimal('5'))])
        for movement_type, material in (
            (StockMovement.MovementType.TRANSFORM_CONSUME, self.raw),
            (StockMovement.MovementType.TRANSFORM_PRODUCE, self.finished),
        ):
            with self.subTest(movement_type=movement_type):
                response = self.client.get(reverse('material_dashboard'), {'movement_type': movement_type})
                rows = response.context['rows']
                self.assertEqual([(row.movement_type, row.material) for row in rows], [(movement_type, material)])
                self.assertEqual(self._row(response, self.finished).filtered_produced, Decimal('5.000'))
                self.assertEqual(self._row(response, self.raw).filtered_consumed, Decimal('5.000'))

    def test_the_movement_type_filter_offers_both_directions(self):
        response = self.client.get(reverse('material_dashboard'))
        self.assertContains(response, 'Druh pohybu')
        self.assertContains(response, '<option value="" selected>Všechny druhy</option>', html=True)
        self.assertContains(response, '<option value="TRANSFORM_CONSUME">Spotřeba</option>', html=True)
        self.assertContains(response, '<option value="TRANSFORM_PRODUCE">Výroba</option>', html=True)

    def test_an_unknown_movement_type_is_an_invalid_filter(self):
        self._job(consumed=[(self.raw, Decimal('5'))], produced=[(self.finished, Decimal('5'))])
        response = self.client.get(reverse('material_dashboard'), {'movement_type': 'ADJUSTMENT'})
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(list(response.context['materials']), [])
        self.assertEqual(len(response.context['rows']), 0)


class TimeWorkedTests(TestCase):
    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.other_worker = User.objects.create_user(username='other', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.other_machine = Machine.objects.create(name='Screener B')

    def _job(
        self, creator, hours, collaborators=(), machine_hours=None, machine=None, status=WorkOrder.Status.APPROVED
    ):
        """A job with `hours` of labour by `creator`, plus `(user, hours)` collaborator pairs. `machine_hours` is
        machine runtime and must not reach the hours summary. Approved by default.
        """
        work_order = WorkOrder.objects.create(created_by=creator, description='job', status=status)
        if hours is not None:
            WorkerHours.objects.create(work_order=work_order, user=creator, hours=hours)
        for user, user_hours in collaborators:
            work_order.collaborators.add(user)
            WorkerHours.objects.create(work_order=work_order, user=user, hours=user_hours)
        if machine_hours is not None:
            MachineUsage.objects.create(work_order=work_order, machine=machine or self.machine, hours=machine_hours)
        return work_order

    def _summary_for(self, response, user):
        for row in response.context['summary']:
            if row['user'] == user:
                return row
        return None

    def test_time_worked_requires_login(self):
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_worker_sees_own_total_hours(self):
        self._job(self.worker, Decimal('2.5'))
        self._job(self.worker, Decimal('1.5'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        row = self._summary_for(response, self.worker)
        self.assertEqual(row['hours'], Decimal('4.00'))
        self.assertEqual(row['orders'], 2)

    def test_worker_summary_excludes_other_people(self):
        self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('8'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual([row['user'] for row in response.context['summary']], [self.worker])

    def test_each_person_is_credited_their_own_typed_hours(self):
        self._job(self.worker, Decimal('3'), collaborators=[(self.other_worker, Decimal('2'))])
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(self._summary_for(response, self.worker)['hours'], Decimal('3.00'))
        self.assertEqual(self._summary_for(response, self.other_worker)['hours'], Decimal('2.00'))

    def test_machine_hours_do_not_reach_the_summary(self):
        # A 3-hour crushing run that took the operator 5 hours of labour: this
        # tab reports the 5, and nothing here is derived from machine runtime.
        self._job(self.worker, Decimal('5'), machine_hours=Decimal('3'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(self._summary_for(response, self.worker)['hours'], Decimal('5.00'))

    def test_collaborator_summary_does_not_leak_creator_total(self):
        # The job was created by someone else, so the creator must not show up
        # on the collaborating worker's screen.
        self._job(self.other_worker, Decimal('3'), collaborators=[(self.worker, Decimal('2'))])
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual([row['user'] for row in response.context['summary']], [self.worker])
        self.assertEqual(self._summary_for(response, self.worker)['hours'], Decimal('2.00'))

    def test_creator_hours_not_multiplied_by_collaborator_count(self):
        # Two collaborators make the participation filter's M2M join return the
        # job twice, so aggregating that queryset directly would credit the
        # creator with 6 hours for one 3-hour job (and list it twice below).
        # `time_worked` re-queries by pk to avoid it; drop that and this fails.
        third_worker = User.objects.create_user(username='third', password='pw', role=User.Role.WORKER)
        work_order = self._job(
            self.worker,
            Decimal('3'),
            collaborators=[(self.other_worker, Decimal('1')), (third_worker, Decimal('1'))],
        )
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        row = self._summary_for(response, self.worker)
        self.assertEqual(row['hours'], Decimal('3.00'))
        self.assertEqual(row['orders'], 1)
        listed = response.context['rows']
        self.assertEqual([o.pk for o in listed], [work_order.pk])
        self.assertEqual(listed[0].total_hours, Decimal('5.00'))

    def test_manager_filtering_to_one_worker_does_not_multiply_hours(self):
        # Same join, reached the other way: the `worker` filter is the manager's
        # route through participation_filter.
        third_worker = User.objects.create_user(username='third', password='pw', role=User.Role.WORKER)
        work_order = self._job(
            self.worker,
            Decimal('3'),
            collaborators=[(self.other_worker, Decimal('1')), (third_worker, Decimal('1'))],
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'), {'worker': self.worker.pk})
        # Only the picked worker: the colleagues on the same job are not „Pracovník".
        self.assertEqual(
            [(row['user'], row['hours']) for row in response.context['summary']], [(self.worker, Decimal('3.00'))]
        )
        self.assertEqual(response.context['total_hours'], Decimal('3.00'))
        # The job list still shows the whole job.
        self.assertEqual([o.pk for o in response.context['rows']], [work_order.pk])
        self.assertEqual(response.context['rows'][0].total_hours, Decimal('5.00'))

    def test_manager_sees_every_worker(self):
        self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('5'))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'))
        users = {row['user'] for row in response.context['summary']}
        self.assertEqual(users, {self.worker, self.other_worker})

    def test_job_without_worker_hours_is_absent_from_the_summary(self):
        # Nothing on the Transform form can produce this — only a work order
        # created in the admin with no hours rows on it.
        work_order = self._job(self.worker, None)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(response.context['summary'], [])
        # It still belongs to the worker, so the job itself stays listed.
        self.assertEqual([o.pk for o in response.context['rows']], [work_order.pk])
        self.assertIsNone(response.context['rows'][0].total_hours)

    def test_worker_filter_hidden_from_worker(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertNotIn('worker', response.context['form'].fields)

    def test_worker_filter_available_to_manager(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'))
        self.assertIn('worker', response.context['form'].fields)

    def test_worker_cannot_bypass_restriction_via_worker_param(self):
        self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('8'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'), {'worker': self.other_worker.pk})
        self.assertNotIn(self.other_worker, [row['user'] for row in response.context['summary']])

    def test_manager_can_filter_to_one_worker(self):
        self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('8'))
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'), {'worker': self.other_worker.pk})
        self.assertEqual([row['user'] for row in response.context['summary']], [self.other_worker])

    def test_filters_by_date_range(self):
        old = self._job(self.worker, Decimal('4'))
        WorkOrder.objects.filter(pk=old.pk).update(performed_on=date.today() - timedelta(days=10))
        self._job(self.worker, Decimal('1'))
        self.client.force_login(self.worker)
        response = self.client.get(
            reverse('time_worked'), {'date_from': (date.today() - timedelta(days=1)).isoformat()}
        )
        self.assertEqual(self._summary_for(response, self.worker)['hours'], Decimal('1.00'))

    def test_shows_nothing_when_filter_is_invalid(self):
        self._job(self.worker, Decimal('4'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'), {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(response.context['summary'], [])
        self.assertEqual(len(response.context['rows']), 0)

    def test_detail_list_scoped_to_worker(self):
        own = self._job(self.worker, Decimal('2'))
        self._job(self.other_worker, Decimal('8'))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual([o.pk for o in response.context['rows']], [own.pk])


class SouhrnFleetTests(TestCase):
    """Souhrn's machine and fuel tables: the fleet's totals for a worker, not the worker's own."""

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.other_worker = User.objects.create_user(username='other', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machine = Machine.objects.create(name='Crusher A')
        self.client.force_login(self.worker)

    def _usage(self, creator, reading, tons, status=WorkOrder.Status.APPROVED, days_ago=0):
        work_order = WorkOrder.objects.create(
            created_by=creator,
            description='job',
            status=status,
            performed_on=date.today() - timedelta(days=days_ago),
        )
        return MachineUsage.objects.create(work_order=work_order, machine=self.machine, hours=reading, tons=tons)

    def _refuel(self, creator, litres, status=Refuelling.Status.APPROVED, days_ago=0):
        refuelling = Refuelling.objects.create(
            created_by=creator, status=status, performed_on=date.today() - timedelta(days=days_ago)
        )
        return MachineRefuel.objects.create(refuelling=refuelling, machine=self.machine, litres=litres)

    def _fleet(self, params=None):
        return self.client.get(reverse('time_worked'), params or {}).context['fleet']

    def test_machine_totals_include_other_peoples_jobs(self):
        self._usage(self.other_worker, Decimal('100'), Decimal('10'))
        self._usage(self.other_worker, Decimal('104'), Decimal('6'))
        machine = self._fleet()['machines'][0]
        self.assertEqual(machine.filtered_hours, Decimal('4.00'))
        self.assertEqual(machine.filtered_tons, Decimal('16.00'))

    def test_fuel_totals_include_other_peoples_refuellings(self):
        self._refuel(self.other_worker, Decimal('50'))
        self._refuel(self.worker, Decimal('20'))
        fleet = self._fleet()
        self.assertEqual(fleet['refuel_machines'][0].filtered_litres, Decimal('70.00'))
        self.assertEqual(fleet['total_litres'], Decimal('70.00'))

    def test_hours_stay_the_workers_own(self):
        # The fleet tables are shared; the labour summary beside them is not.
        self._usage(self.other_worker, Decimal('100'), Decimal('10'))
        WorkerHours.objects.create(work_order=WorkOrder.objects.get(), user=self.other_worker, hours=Decimal('8'))
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(response.context['summary'], [])
        self.assertEqual(len(response.context['rows']), 0)
        self.assertEqual(response.context['fleet']['machines'][0].filtered_tons, Decimal('10.00'))

    def test_pending_entries_are_not_counted(self):
        self._usage(self.other_worker, Decimal('100'), Decimal('10'), status=WorkOrder.Status.PENDING)
        self._refuel(self.other_worker, Decimal('50'), status=Refuelling.Status.PENDING)
        fleet = self._fleet()
        self.assertIsNone(fleet['machines'][0].filtered_tons)
        self.assertEqual(fleet['total_litres'], Decimal('0'))

    def test_totals_follow_the_date_filter(self):
        self._usage(self.other_worker, Decimal('100'), Decimal('10'), days_ago=40)
        self._usage(self.other_worker, Decimal('103'), Decimal('6'))
        self._refuel(self.other_worker, Decimal('50'), days_ago=40)
        self._refuel(self.other_worker, Decimal('20'))
        fleet = self._fleet({'date_from': (date.today() - timedelta(days=7)).isoformat()})
        self.assertEqual(fleet['machines'][0].filtered_tons, Decimal('6.00'))
        self.assertEqual(fleet['machines'][0].filtered_hours, Decimal('3.00'))
        self.assertEqual(fleet['total_litres'], Decimal('20.00'))

    def test_only_the_dates_apply(self):
        # Stroje's machine and author filters are not on this page; passing them changes nothing.
        other_machine = Machine.objects.create(name='Screener B')
        self._usage(self.other_worker, Decimal('100'), Decimal('10'))
        fleet = self._fleet({'machine': other_machine.pk, 'created_by': self.worker.pk})
        self.assertEqual([m.name for m in fleet['machines']], ['Crusher A', 'Screener B'])
        self.assertEqual(fleet['machines'][0].filtered_tons, Decimal('10.00'))

    def test_an_invalid_filter_shows_no_machines(self):
        self._usage(self.other_worker, Decimal('100'), Decimal('10'))
        fleet = self._fleet({'date_from': 'not-a-date'})
        self.assertEqual(list(fleet['machines']), [])
        self.assertEqual(list(fleet['refuel_machines']), [])

    def test_page_shows_both_tables(self):
        self._usage(self.other_worker, Decimal('100'), Decimal('12.5'))
        response = self.client.get(reverse('time_worked'))
        self.assertContains(response, '<h1>Souhrn</h1>', html=True)
        self.assertContains(response, 'Natankováno (l)')
        self.assertContains(response, '12,50')
        # No prices for workers.
        self.assertNotContains(response, 'Kč')

    def test_managers_do_not_get_the_tables(self):
        # They have the same numbers, and more, on Stroje.
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'))
        self.assertIsNone(response.context['fleet'])
        self.assertNotContains(response, 'Natankováno (l)')

    def test_nav_names_the_page_souhrn(self):
        response = self.client.get(reverse('transform_create'))
        self.assertContains(response, f'<a href="{reverse("time_worked")}">Souhrn</a>', html=True)


APPROVED = WorkOrder.Status.APPROVED


class ShowMoreTests(TestCase):
    """Report tables start at PREVIEW_ROWS rows; each „Zobrazit více" adds MORE_ROWS, and exports always hold every row."""

    MANY = PREVIEW_ROWS + 2

    def setUp(self):
        self.worker = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        self.machines = [Machine.objects.create(name=f'Stroj {i:02}') for i in range(self.MANY)]
        material = Material.objects.create(sku='RAW', name='Štěrk')
        for i in range(self.MANY):
            work_order = WorkOrder.objects.create(created_by=self.worker, description=f'job {i}', status=APPROVED)
            WorkerHours.objects.create(work_order=work_order, user=self.worker, hours=Decimal('1'))
            MachineUsage.objects.create(work_order=work_order, machine=self.machines[0], hours=100 + i)
            StockMovement.objects.create(
                material=material,
                quantity=-1,
                movement_type=StockMovement.MovementType.TRANSFORM_CONSUME,
                work_order=work_order,
                created_by=self.worker,
            )
            refuelling = Refuelling.objects.create(created_by=self.worker, status=APPROVED)
            MachineRefuel.objects.create(refuelling=refuelling, machine=self.machines[0], litres=Decimal('10'))

    def _tables(self, url_name, user, params=None):
        self.client.force_login(user)
        if isinstance(params, str):
            response = self.client.get(f'{reverse(url_name)}?{params}')
        else:
            response = self.client.get(reverse(url_name), params or {})
        return response, response.context['tables']

    def test_every_table_starts_cut_to_the_preview(self):
        pages = (
            ('time_worked', self.worker, ('machines', 'refuels', 'jobs')),
            ('machine_dashboard', self.manager, ('machines', 'refuels', 'refuel_rows', 'usages')),
            ('material_dashboard', self.manager, ('movements',)),
        )
        for url_name, user, keys in pages:
            response, tables = self._tables(url_name, user)
            for key in keys:
                with self.subTest(page=url_name, table=key):
                    table = tables[key]
                    self.assertEqual(len(table['rows']), PREVIEW_ROWS)
                    self.assertEqual(table['total'], self.MANY)
                    self.assertEqual(table['more'], self.MANY - PREVIEW_ROWS)
                    self.assertContains(response, f'id="{table["anchor"]}"')
                    self.assertContains(response, f'Zobrazit více ({self.MANY - PREVIEW_ROWS})')
                    self.assertContains(response, f'Zobrazeno {PREVIEW_ROWS} z {self.MANY} záznamů')

    def test_a_short_table_has_no_button(self):
        response, tables = self._tables('material_dashboard', self.manager)
        self.assertEqual(len(tables['materials']['rows']), 1)
        self.assertEqual(tables['materials']['more'], 0)
        self.assertContains(response, 'Zobrazeno 1 z 1 záznamu')
        _, tables = self._tables('time_worked', self.worker)
        self.assertEqual(tables['hours']['more'], 0)

    def _bulk_usages(self, count):
        work_order = WorkOrder.objects.create(created_by=self.worker, description='bulk', status=APPROVED)
        MachineUsage.objects.bulk_create(
            MachineUsage(work_order=work_order, machine=self.machines[1], hours=1000 + i) for i in range(count)
        )

    def test_each_click_shows_at_most_more_rows(self):
        self._bulk_usages(MORE_ROWS + 30)
        total = self.MANY + MORE_ROWS + 30
        response, tables = self._tables('machine_dashboard', self.manager)
        self.assertContains(response, f'Zobrazit více ({MORE_ROWS})')
        response, tables = self._tables('machine_dashboard', self.manager, tables['usages']['more_querystring'])
        table = tables['usages']
        self.assertEqual(len(table['rows']), PREVIEW_ROWS + MORE_ROWS)
        self.assertEqual(table['more'], total - PREVIEW_ROWS - MORE_ROWS)
        self.assertContains(response, f'Zobrazit více ({total - PREVIEW_ROWS - MORE_ROWS})')
        self.assertContains(response, f'Zobrazeno {PREVIEW_ROWS + MORE_ROWS} z {total} záznamů')
        response, tables = self._tables('machine_dashboard', self.manager, tables['usages']['more_querystring'])
        self.assertEqual(len(tables['usages']['rows']), total)
        self.assertEqual(tables['usages']['more'], 0)
        self.assertNotContains(response, '#tabulka-usages">Zobrazit více')

    def test_more_grows_only_its_own_table(self):
        _, tables = self._tables('machine_dashboard', self.manager, {'vice_usages': '1'})
        self.assertEqual(len(tables['usages']['rows']), self.MANY)
        self.assertEqual(len(tables['machines']['rows']), PREVIEW_ROWS)

    def test_the_link_keeps_the_filters_and_the_tables_already_grown(self):
        date_from = (date.today() - timedelta(days=7)).isoformat()
        response, tables = self._tables('machine_dashboard', self.manager, {'date_from': date_from, 'vice_usages': '1'})
        querystring = tables['machines']['more_querystring']
        self.assertIn(f'date_from={date_from}', querystring)
        self.assertIn('vice_usages=1', querystring)
        self.assertIn('vice_machines=1', querystring)
        self.assertContains(response, f'href="?{querystring.replace("&", "&amp;")}#tabulka-machines"')
        _, tables = self._tables('machine_dashboard', self.manager, querystring)
        self.assertEqual(len(tables['usages']['rows']), self.MANY)
        self.assertEqual(len(tables['machines']['rows']), self.MANY)

    def test_the_export_links_ignore_the_table_sizes(self):
        response, _ = self._tables('material_dashboard', self.manager, {'vice_movements': '1', 'location': ''})
        self.assertEqual(response.context['querystring'], 'location=')

    def test_a_tampered_count_is_clamped(self):
        for value in ('-3', 'x', '99999999999999999999999'):
            with self.subTest(value=value):
                _, tables = self._tables('machine_dashboard', self.manager, {'vice_usages': value})
                self.assertIn(len(tables['usages']['rows']), (PREVIEW_ROWS, self.MANY))
        _, tables = self._tables('machine_dashboard', self.manager, {'vice_usages': '99999999999999999999999'})
        self.assertEqual(tables['usages']['more_querystring'], 'vice_usages=2')

    def test_a_grown_table_offers_to_collapse(self):
        response, tables = self._tables('time_worked', self.worker, {'vice_jobs': '1', 'vice_machines': '1'})
        table = tables['jobs']
        self.assertTrue(table['can_collapse'])
        self.assertContains(response, 'Zobrazit méně')
        # Collapsing takes out only this table's count.
        self.assertEqual(table['collapse_querystring'], 'vice_machines=1')
        _, tables = self._tables('time_worked', self.worker, table['collapse_querystring'])
        self.assertEqual(len(tables['jobs']['rows']), PREVIEW_ROWS)
        self.assertEqual(len(tables['machines']['rows']), self.MANY)

    def test_a_short_table_has_no_collapse_button(self):
        response, tables = self._tables('material_dashboard', self.manager, {'vice_materials': '1'})
        self.assertFalse(tables['materials']['can_collapse'])
        self.assertNotContains(response, 'Zobrazit méně')

    def test_prehled_lists_start_cut_and_grow(self):
        for url_name, key in (('job_dashboard', 'jobs'), ('refuel_dashboard', 'refuellings')):
            with self.subTest(page=url_name):
                response, tables = self._tables(url_name, self.manager)
                self.assertEqual(len(tables[key]['rows']), PREVIEW_ROWS)
                self.assertContains(response, f'Zobrazit více ({self.MANY - PREVIEW_ROWS})')
                response, tables = self._tables(url_name, self.manager, {f'vice_{key}': '1'})
                self.assertEqual(len(tables[key]['rows']), self.MANY)
                self.assertContains(response, 'Zobrazit méně')

    def test_exports_hold_every_row(self):
        self.client.force_login(self.manager)
        for url_name in ('machine_dashboard_export', 'machine_usage_detail_export', 'machine_refuel_detail_export'):
            with self.subTest(export=url_name):
                lines = self.client.get(reverse(url_name)).content.decode('utf-8-sig').strip().split('\r\n')
                self.assertEqual(len(lines) - 1, self.MANY)


class DatePresetTests(ReviewFixtureMixin, TestCase):
    """The quick date ranges above every filter form.

    They are links, not a form field, so what they have to get right is the
    querystring: the dates themselves, everything else the user already picked,
    and dropping `page`.
    """

    LABELS = ('Vše', 'Posledních 7 dní', 'Posledních 30 dní', 'Minulý měsíc')
    PAGES = ('machine_dashboard', 'material_dashboard', 'time_worked', 'job_dashboard')

    def preset(self, response, key):
        return next(row for row in response.context['date_presets'] if row['key'] == key)

    def test_every_filtered_page_offers_the_same_ranges(self):
        # The manager sees all three; a worker only reaches two of them, and
        # those are covered by the scoping tests elsewhere.
        self.client.force_login(self.manager)
        for name in self.PAGES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual([row['label'] for row in response.context['date_presets']], list(self.LABELS))
                for label in self.LABELS:
                    self.assertContains(response, label)

    def test_the_ranges_end_today_and_include_it(self):
        today = timezone.localdate()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        for key, days in (('7d', 6), ('30d', 29)):
            with self.subTest(preset=key):
                querystring = self.preset(response, key)['querystring']
                self.assertIn(f'date_from={today - timedelta(days=days)}', querystring)
                self.assertIn(f'date_to={today}', querystring)

    def test_last_month_is_the_whole_previous_calendar_month(self):
        self.assertEqual(_last_month(date(2026, 3, 15)), (date(2026, 2, 1), date(2026, 2, 28)))
        # Across a year boundary, and onto a 31-day month.
        self.assertEqual(_last_month(date(2026, 1, 1)), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_a_range_actually_filters(self):
        recent = self.submit_job(self.worker)
        old = self.submit_job(self.worker)
        WorkOrder.objects.filter(pk=old.pk).update(performed_on=date.today() - timedelta(days=45))
        self.client.force_login(self.manager)
        querystring = self.preset(self.client.get(reverse('job_dashboard')), '30d')['querystring']
        response = self.client.get(f'{reverse("job_dashboard")}?{querystring}')
        self.assertEqual(list(response.context['rows']), [recent])

    def test_vse_clears_the_range(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'date_from': '2026-01-01', 'date_to': '2026-01-31'})
        self.assertEqual(self.preset(response, 'all')['querystring'], '')

    def test_the_range_in_effect_is_the_active_one(self):
        today = timezone.localdate()
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse('job_dashboard'),
            {'date_from': (today - timedelta(days=6)).isoformat(), 'date_to': today.isoformat()},
        )
        self.assertEqual([row['key'] for row in response.context['date_presets'] if row['active']], ['7d'])

    def test_nothing_is_active_until_a_range_is_picked(self):
        # "Vše" is: no dates at all, which is where an unfiltered page starts.
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual([row['key'] for row in response.context['date_presets'] if row['active']], ['all'])

    def test_the_other_filters_survive_the_tap(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'created_by': self.worker.pk, 'vice_jobs': '2'})
        querystring = self.preset(response, '7d')['querystring']
        self.assertIn(f'created_by={self.worker.pk}', querystring)
        # A new range starts with the tables cut back.
        self.assertNotIn('vice_', querystring)
