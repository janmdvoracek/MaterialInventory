"""Reviewing jobs: approve, edit, delete, and what the worker sees."""

from decimal import Decimal

from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from accounts.models import User
from materials.models import Material
from workorders.models import MachineUsage, StockMovement, WorkerHours, WorkOrder
from workorders.reports import MY_JOBS_LIMIT

from .helpers import ReviewFixtureMixin, job_payload


class JobReviewTests(ReviewFixtureMixin, TestCase):
    def test_worker_submission_waits_for_approval(self):
        work_order = self.submit_job(self.worker)
        self.assertEqual(work_order.status, WorkOrder.Status.PENDING)
        self.assertIsNone(work_order.reviewed_at)
        self.assertIsNone(work_order.reviewed_by)

    def test_manager_submission_is_approved_on_the_spot(self):
        # Nobody above a manager signs their work off, so waiting for approval
        # would leave it stuck forever.
        work_order = self.submit_job(self.manager)
        self.assertEqual(work_order.status, WorkOrder.Status.APPROVED)
        self.assertEqual(work_order.reviewed_by, self.manager)
        self.assertIsNotNone(work_order.reviewed_at)

    def test_superuser_submission_is_approved_on_the_spot(self):
        # createsuperuser leaves role=WORKER, and is_manager_or_admin covers it.
        admin_user = User.objects.create_superuser(username='root', password='pw')
        work_order = self.submit_job(admin_user)
        self.assertEqual(work_order.status, WorkOrder.Status.APPROVED)

    def test_worker_is_told_the_job_awaits_approval(self):
        # follow=True because the message is consumed by the page the redirect
        # lands on, which is where the worker actually reads it.
        self.client.force_login(self.worker)
        response = self.client.post(
            reverse('transform_create'),
            job_payload(
                [{'material': self.material_raw, 'quantity': Decimal('5')}],
                [{'material': self.material_finished, 'quantity': Decimal('5')}],
            ),
            follow=True,
        )
        self.assertContains(response, 'čeká na schválení')

    def test_pending_job_is_absent_from_time_worked(self):
        self.submit_job(self.worker, hours='3')
        self.client.force_login(self.manager)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(response.context['summary'], [])

    def test_pending_job_is_absent_from_machine_history(self):
        self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a, 'hours': '2'}])
        self.client.force_login(self.manager)
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(len(response.context['rows']), 0)

    def test_approval_lets_the_job_into_the_reports(self):
        work_order = self.submit_job(self.worker, hours='3', machine_rows=[{'machine': self.machine_a, 'hours': '2'}])
        self.client.force_login(self.manager)
        self.client.post(reverse('job_approve', args=[work_order.pk]))

        response = self.client.get(reverse('time_worked'))
        self.assertEqual([row['user'] for row in response.context['summary']], [self.worker])
        response = self.client.get(reverse('machine_dashboard'))
        self.assertEqual(len(response.context['rows']), 1)
        machine = next(m for m in response.context['machines'] if m == self.machine_a)
        self.assertEqual(machine.filtered_reading, Decimal('2.0'))

    def test_approve_records_who_signed_it_off(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_approve', args=[work_order.pk]))
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))
        work_order.refresh_from_db()
        self.assertEqual(work_order.status, WorkOrder.Status.APPROVED)
        self.assertEqual(work_order.reviewed_by, self.manager)
        self.assertIsNotNone(work_order.reviewed_at)

    def test_the_way_back_is_at_the_top_and_the_bottom(self):
        # Approving reloads the page at the top; the bottom link is a long scroll away on a phone.
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_detail', args=[work_order.pk]))
        self.assertContains(response, f'<a href="{reverse("job_dashboard")}">← Zpět na přehled</a>', count=2)

    def test_approve_rejects_a_get(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_approve', args=[work_order.pk]))
        self.assertEqual(response.status_code, 405)
        work_order.refresh_from_db()
        self.assertEqual(work_order.status, WorkOrder.Status.PENDING)

    def test_a_job_can_only_be_pending_or_approved(self):
        # Returning a job to its author is gone: a manager corrects it or
        # deletes it. Nothing else may end up in `status`.
        self.assertEqual([value for value, _ in WorkOrder.Status.choices], ['PENDING', 'APPROVED'])

    def test_there_is_no_return_url(self):
        with self.assertRaises(NoReverseMatch):
            reverse('job_return', args=[1])

    def test_the_detail_page_offers_only_approve_edit_and_delete(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_detail', args=[work_order.pk]))
        self.assertContains(response, 'Schválit')
        self.assertContains(response, 'Upravit')
        self.assertContains(response, 'Smazat')
        self.assertNotContains(response, 'Vrátit')

    def test_review_pages_are_closed_to_workers(self):
        # Hiding the nav entry is not access control: the views are gated too.
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.worker)
        for name, method in (
            ('job_dashboard', 'get'),
            ('job_detail', 'get'),
            ('job_edit', 'get'),
            ('job_delete', 'get'),
            ('job_approve', 'post'),
        ):
            with self.subTest(view=name):
                url = reverse(name) if name == 'job_dashboard' else reverse(name, args=[work_order.pk])
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 403)

    def test_review_pages_require_login(self):
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)


class MyJobsTests(ReviewFixtureMixin, TestCase):
    """A worker's recent submissions at the top of Souhrn: their only feedback on review."""

    def test_author_sees_their_own_pending_job(self):
        work_order = self.submit_job(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [work_order])
        self.assertContains(response, 'Čeká na schválení')

    def test_the_transform_page_no_longer_carries_the_list(self):
        self.submit_job(self.worker)
        response = self.client.get(reverse('transform_create'))
        self.assertNotIn('my_jobs', response.context)
        self.assertNotContains(response, 'Moje poslední zápisy')

    def test_a_manager_is_not_shown_the_list(self):
        # Souhrn is the whole depot's report for them, and Přehled already
        # lists every job with its status.
        self.submit_job(self.manager)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [])
        self.assertNotContains(response, 'Moje poslední zápisy')

    def test_the_status_follows_the_review(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        self.client.post(reverse('job_approve', args=[work_order.pk]))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertContains(response, 'Schváleno')

    def test_a_deleted_job_drops_off_the_list(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        self.client.post(reverse('job_delete', args=[work_order.pk]))
        self.client.force_login(self.worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [])

    def test_someone_elses_job_is_not_listed(self):
        self.submit_job(self.worker)
        self.client.force_login(self.other_worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [])

    def test_a_job_someone_named_you_on_is_not_listed(self):
        # Submissions only — a collaborator did not record the job. Their hours
        # from it show up in the summary below once it is approved.
        self.submit_job(self.worker, worker_rows=[{'user': self.other_worker, 'hours': '2'}])
        self.client.force_login(self.other_worker)
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(list(response.context['my_jobs']), [])

    def test_the_hours_shown_are_the_authors_own(self):
        # Not the job total: the collaborator's 2 h belong to them, not here.
        self.submit_job(self.worker, hours='3', worker_rows=[{'user': self.other_worker, 'hours': '2'}])
        response = self.client.get(reverse('time_worked'))
        self.assertEqual(response.context['my_jobs'][0].my_hours, Decimal('3.00'))

    def test_the_list_ignores_the_hours_filters(self):
        # The filters scope the report below; a pending job is exactly what that
        # report cannot show, so the list must not be scoped away with it.
        work_order = self.submit_job(self.worker)
        response = self.client.get(reverse('time_worked'), {'date_from': '2000-01-01', 'date_to': '2000-01-02'})
        self.assertEqual(response.context['summary'], [])
        self.assertEqual(list(response.context['my_jobs']), [work_order])

    def test_only_the_most_recent_jobs_are_listed(self):
        for _ in range(MY_JOBS_LIMIT + 2):
            newest = self.submit_job(self.worker)
        response = self.client.get(reverse('time_worked'))
        my_jobs = list(response.context['my_jobs'])
        self.assertEqual(len(my_jobs), MY_JOBS_LIMIT)
        self.assertEqual(my_jobs[0], newest)


class JobDashboardTests(ReviewFixtureMixin, TestCase):
    def test_dashboard_lists_every_job_whoever_recorded_it(self):
        mine = self.submit_job(self.manager)
        theirs = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual(set(response.context['rows']), {mine, theirs})

    def test_dashboard_highlights_jobs_awaiting_review(self):
        self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        self.assertContains(response, 'row-pending')
        self.assertEqual(response.context['pending_count'], 1)

    def test_dashboard_shows_the_hours_of_everyone_on_the_job(self):
        self.submit_job(self.worker, hours='3', worker_rows=[{'user': self.other_worker, 'hours': '2'}])
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'))
        self.assertEqual(response.context['rows'][0].total_hours, Decimal('5.00'))

    def test_dashboard_filters_by_status(self):
        approved = self.submit_job(self.manager)
        self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'status': WorkOrder.Status.APPROVED})
        self.assertEqual(list(response.context['rows']), [approved])

    def test_dashboard_filters_by_author(self):
        self.submit_job(self.manager)
        theirs = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'created_by': self.worker.pk})
        self.assertEqual(list(response.context['rows']), [theirs])

    def test_dashboard_shows_no_rows_when_filter_is_invalid(self):
        # An unusable filter must not fall through to showing everything.
        self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_dashboard'), {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(len(response.context['rows']), 0)

    def test_detail_shows_everything_that_was_filled_in(self):
        work_order = self.submit_job(
            self.worker,
            quantity=Decimal('7.5'),
            hours='3',
            machine_rows=[{'machine': self.machine_a, 'hours': '2', 'tons': '4.25'}],
            worker_rows=[{'user': self.other_worker, 'hours': '1.5'}],
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_detail', args=[work_order.pk]))
        self.assertEqual(len(response.context['consumed']), 1)
        self.assertEqual(len(response.context['produced']), 1)
        # Consumed quantities are stored negative; the reviewer sees what was typed.
        self.assertEqual(response.context['consumed'][0].typed_quantity, Decimal('7.5'))
        self.assertEqual(len(response.context['machine_usages']), 1)
        self.assertEqual(len(response.context['worker_hours']), 2)
        self.assertContains(response, 'Crusher A')
        self.assertContains(response, '4,25')


class JobEditTests(ReviewFixtureMixin, TestCase):
    def _edit(self, work_order, **kwargs):
        self.client.force_login(self.manager)
        return self.client.post(reverse('job_edit', args=[work_order.pk]), job_payload(**kwargs))

    def test_edit_form_is_prefilled_with_what_was_recorded(self):
        work_order = self.submit_job(
            self.worker, quantity=Decimal('5'), hours='3', machine_rows=[{'machine': self.machine_a, 'hours': '2'}]
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[work_order.pk]))
        self.assertEqual(response.context['order_form'].initial['hours'], Decimal('3'))
        self.assertEqual(
            response.context['consumed_formset'].initial,
            [{'material': self.material_raw.pk, 'quantity': Decimal('5')}],
        )
        self.assertEqual(response.context['machine_formset'].initial[0]['hours'], Decimal('2'))

    def test_prefilled_form_can_be_saved_unchanged(self):
        # The models store more decimal places than the form accepts, so a raw
        # prefill would make the edit form reject its own values.
        work_order = self.submit_job(
            self.worker, quantity=Decimal('5'), hours='1.5', machine_rows=[{'machine': self.machine_a, 'hours': '2'}]
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[work_order.pk]))
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': '2'}],
            hours=str(response.context['order_form'].initial['hours']),
        )
        response = self.client.post(reverse('job_edit', args=[work_order.pk]), data)
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))

    def test_edit_rewrites_the_line_items(self):
        work_order = self.submit_job(self.worker, quantity=Decimal('5'))
        response = self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('8')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('8')}],
            description='Opraveno',
        )
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))
        work_order.refresh_from_db()
        self.assertEqual(work_order.description, 'Opraveno')
        self.assertEqual(work_order.movements.count(), 2)
        consumed = work_order.movements.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.quantity, Decimal('-8'))
        # The rows still belong to whoever did the work, not to the reviewer.
        self.assertEqual(consumed.created_by, self.worker)

    def test_edit_does_not_approve_the_job(self):
        work_order = self.submit_job(self.worker)
        self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        work_order.refresh_from_db()
        self.assertEqual(work_order.status, WorkOrder.Status.PENDING)

    def test_edit_rejects_unbalanced_totals(self):
        work_order = self.submit_job(self.worker, quantity=Decimal('5'))
        response = self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('8')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        self.assertEqual(response.status_code, 200)
        consumed = work_order.movements.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.quantity, Decimal('-5'))

    def test_edit_rewrites_the_hours_and_collaborators(self):
        work_order = self.submit_job(self.worker, hours='3', worker_rows=[{'user': self.other_worker, 'hours': '2'}])
        self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            hours='4',
        )
        self.assertEqual(
            {(row.user, row.hours) for row in work_order.worker_hours.all()},
            {(self.worker, Decimal('4.00'))},
        )
        # Collaborators stay derived from the hours rows.
        self.assertEqual(list(work_order.collaborators.all()), [])

    def test_edit_replaces_the_machine_rows(self):
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a, 'hours': '3'}])
        self._edit(
            work_order,
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_b, 'hours': '5'}],
        )
        self.assertEqual(
            [(u.machine, u.hours) for u in work_order.machine_usages.all()],
            [(self.machine_b, Decimal('5.00'))],
        )

    def test_collaborator_choices_exclude_the_author_not_the_reviewer(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[work_order.pk]))
        choices = set(response.context['worker_formset'].forms[0].fields['user'].queryset)
        self.assertNotIn(self.worker, choices)
        self.assertIn(self.other_worker, choices)


class JobDeleteTests(ReviewFixtureMixin, TestCase):
    def test_confirmation_page_lists_what_will_go(self):
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a, 'hours': '3'}])
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_delete', args=[work_order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['machine_usages']), 1)
        self.assertTrue(WorkOrder.objects.filter(pk=work_order.pk).exists())

    def test_delete_removes_the_job_and_all_its_rows(self):
        work_order = self.submit_job(self.worker, machine_rows=[{'machine': self.machine_a, 'hours': '3'}], hours='2')
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_delete', args=[work_order.pk]))
        self.assertRedirects(response, reverse('job_dashboard'))
        self.assertFalse(WorkOrder.objects.filter(pk=work_order.pk).exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())


class RetiredCatalogOnEditTests(ReviewFixtureMixin, TestCase):
    """A job naming a since-retired material or machine stays editable, and saving it keeps the row.

    Otherwise the picker renders blank and saving deletes the row, or leaves the job stuck behind a balance error.
    """

    def _retire(self, record):
        record.is_active = False
        record.save(update_fields=['is_active'])

    def test_retired_machine_still_renders_on_the_row_it_is_on(self):
        job = self.submit_job(
            self.worker,
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}],
        )
        self._retire(self.machine_a)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[job.pk]))
        row = response.context['machine_formset'].forms[0]
        self.assertIn(self.machine_a, row.fields['machine'].queryset)
        self.assertIn('Crusher A', response.content.decode())

    def test_retiring_a_machine_does_not_delete_its_usage_on_the_next_edit(self):
        job = self.submit_job(
            self.worker,
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}],
        )
        self._retire(self.machine_a)
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}],
            description='opraveno',
        )
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_edit', args=[job.pk]), payload)
        self.assertRedirects(response, reverse('job_detail', args=[job.pk]))
        usage = MachineUsage.objects.get(work_order=job)
        self.assertEqual(usage.machine, self.machine_a)
        self.assertEqual(usage.hours, Decimal('2.00'))

    def test_a_job_naming_a_retired_material_can_still_be_corrected(self):
        job = self.submit_job(self.worker)
        self._retire(self.material_raw)
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            description='opraveno',
        )
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_edit', args=[job.pk]), payload)
        self.assertRedirects(response, reverse('job_detail', args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.description, 'opraveno')
        consumed = job.movements.get(movement_type=StockMovement.MovementType.TRANSFORM_CONSUME)
        self.assertEqual(consumed.material, self.material_raw)

    def test_a_retired_record_is_still_kept_off_a_new_job(self):
        # The widening is scoped to the job being edited; recording a *new* job
        # must not offer a retired material, which is the whole point of
        # retiring it.
        self._retire(self.material_raw)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        row = response.context['consumed_formset'].forms[0]
        self.assertNotIn(self.material_raw, row.fields['material'].queryset)

    def test_the_edit_form_offers_no_other_retired_record(self):
        # Only what this job already names comes back, not the retired catalog.
        other = Material.objects.create(sku='OLD', name='Vyřazený písek', is_active=False)
        job = self.submit_job(self.worker)
        self._retire(self.material_raw)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[job.pk]))
        queryset = response.context['consumed_formset'].forms[0].fields['material'].queryset
        self.assertIn(self.material_raw, queryset)
        self.assertNotIn(other, queryset)
