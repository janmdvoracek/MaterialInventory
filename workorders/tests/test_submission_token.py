"""Sending the same entry form more than once saves one entry."""

from decimal import Decimal
from threading import Thread
from uuid import uuid4

from django.contrib.messages import get_messages
from django.db import connection, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from workorders.kinds import JOB
from workorders.models import JobPhoto, Refuelling, StockMovement, WorkOrder

from .helpers import ReviewFixtureMixin, job_payload, refuel_payload

ALREADY_SAVED_JOB = 'Toto zpracování už je zaznamenané, podruhé se neuložilo.'
ALREADY_SAVED_REFUELLING = 'Toto tankování už je zaznamenané, podruhé se neuložilo.'


class SubmissionTokenTests(ReviewFixtureMixin, TestCase):
    def _job_data(self, token=None, **extra):
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            **extra,
        )
        if token is not None:
            data['submission_token'] = str(token)
        return data

    def _post_job(self, data):
        return self.client.post(reverse('transform_create'), data)

    def _messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_each_render_of_a_new_form_carries_its_own_token(self):
        self.client.force_login(self.worker)
        for name in ('transform_create', 'refuel_create'):
            first = self.client.get(reverse(name))
            second = self.client.get(reverse(name))
            self.assertNotEqual(first.context['submission_token'], second.context['submission_token'])
            self.assertContains(
                first, f'<input type="hidden" name="submission_token" value="{first.context["submission_token"]}">'
            )

    def test_edit_pages_render_no_token(self):
        job = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reverse('job_edit', args=[job.pk])), 'name="submission_token"')

    def test_a_repeated_job_saves_once_and_says_so(self):
        self.client.force_login(self.worker)
        data = self._job_data(uuid4())
        self.assertRedirects(self._post_job(data), reverse('transform_create'))
        response = self._post_job(data)
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertIn(ALREADY_SAVED_JOB, self._messages(response))

    def test_twelve_sends_save_one_job(self):
        """The incident behind the token: one worker's form, saved twelve times in eight minutes."""
        self.client.force_login(self.worker)
        data = self._job_data(uuid4())
        for _ in range(12):
            self._post_job(data)
        self.assertEqual(WorkOrder.objects.count(), 1)

    def test_a_managers_repeat_is_not_approved_twice(self):
        """A manager's own job counts in reports at once, so a copy would do harm straight away."""
        self.client.force_login(self.manager)
        data = self._job_data(uuid4())
        self._post_job(data)
        self._post_job(data)
        self.assertEqual(WorkOrder.objects.filter(status=WorkOrder.Status.APPROVED).count(), 1)

    def test_different_tokens_save_separate_jobs(self):
        """Two identical jobs are legitimate; only the same form sent twice is a repeat."""
        self.client.force_login(self.worker)
        self._post_job(self._job_data(uuid4()))
        self._post_job(self._job_data(uuid4()))
        self.assertEqual(WorkOrder.objects.count(), 2)

    def test_the_saved_job_records_its_token(self):
        self.client.force_login(self.worker)
        token = uuid4()
        self._post_job(self._job_data(token))
        self.assertEqual(WorkOrder.objects.get().submission_token, token)

    def test_a_page_without_a_token_still_saves(self):
        """A form opened before the deploy has none, and must not lose its job."""
        self.client.force_login(self.worker)
        data = self._job_data()
        self.assertRedirects(self._post_job(data), reverse('transform_create'))
        self.assertRedirects(self._post_job(data), reverse('transform_create'))
        self.assertEqual(WorkOrder.objects.count(), 2)
        self.assertIsNone(WorkOrder.objects.first().submission_token)

    def test_an_unreadable_token_saves_without_an_error_nobody_could_see(self):
        self.client.force_login(self.worker)
        self.assertRedirects(self._post_job(self._job_data('not-a-uuid')), reverse('transform_create'))
        self.assertIsNone(WorkOrder.objects.get().submission_token)

    def test_a_refused_submission_keeps_its_token(self):
        """Nothing was saved, so the corrected form is still the same submission."""
        self.client.force_login(self.worker)
        token = uuid4()
        data = self._job_data(token)
        data['description'] = ''
        response = self._post_job(data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['submission_token'], token)
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_a_row_button_keeps_the_token(self):
        self.client.force_login(self.worker)
        token = uuid4()
        response = self._post_job(self._job_data(token, machine_rows=[]) | {'add_machines': ''})
        self.assertEqual(response.context['submission_token'], token)
        self.assertContains(response, f'value="{token}"')

    def test_a_row_button_after_a_save_does_not_report_a_repeat(self):
        self.client.force_login(self.worker)
        token = uuid4()
        self._post_job(self._job_data(token))
        response = self._post_job(self._job_data(token) | {'add_machines': ''})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(ALREADY_SAVED_JOB, self._messages(response))

    def test_a_repeat_is_answered_before_validation(self):
        """The first send saved; an error on the second must not suggest it didn't."""
        self.client.force_login(self.worker)
        token = uuid4()
        self._post_job(self._job_data(token))
        data = self._job_data(token)
        data['description'] = ''
        response = self._post_job(data)
        self.assertRedirects(response, reverse('transform_create'))
        self.assertIn(ALREADY_SAVED_JOB, self._messages(response))

    def test_two_copies_in_flight_save_one_job(self):
        """Both passed `already_saved` before either saved: the unique column settles it.

        Stood in for by a job appearing between validating the page and saving it.
        """
        token = uuid4()
        request = RequestFactory().post(reverse('transform_create'), self._job_data(token))
        request.user = self.worker
        page = JOB.page(request)
        self.assertTrue(page.is_valid())
        self.assertFalse(JOB.already_saved(page))
        WorkOrder.objects.create(created_by=self.worker, description='Vítěz', submission_token=token)

        self.assertIsNone(JOB.save(page))

        self.assertEqual(WorkOrder.objects.get().description, 'Vítěz')
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(JobPhoto.objects.count(), 0)

    def test_a_repeated_refuelling_saves_once_and_says_so(self):
        self.client.force_login(self.worker)
        data = refuel_payload([{'machine': self.machine_a, 'litres': '10'}]) | {'submission_token': str(uuid4())}
        self.client.post(reverse('refuel_create'), data)
        response = self.client.post(reverse('refuel_create'), data)
        self.assertRedirects(response, reverse('refuel_create'))
        self.assertEqual(Refuelling.objects.count(), 1)
        self.assertIn(ALREADY_SAVED_REFUELLING, self._messages(response))

    def test_a_job_and_a_refuelling_do_not_share_tokens(self):
        """Each kind checks its own table, so neither can swallow the other."""
        self.client.force_login(self.worker)
        token = str(uuid4())
        self._post_job(self._job_data(token))
        self.client.post(
            reverse('refuel_create'),
            refuel_payload([{'machine': self.machine_a, 'litres': '10'}]) | {'submission_token': token},
        )
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertEqual(Refuelling.objects.count(), 1)


class ConcurrentSubmissionTests(ReviewFixtureMixin, TransactionTestCase):
    """Two copies of a form on the server at once, on two real database connections."""

    def test_the_second_copy_waits_for_the_first_and_saves_nothing(self):
        token = uuid4()
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        ) | {'submission_token': str(token)}
        client = Client()
        client.force_login(self.worker)
        responses = []

        def second_copy():
            try:
                responses.append(client.post(reverse('transform_create'), data))
            finally:
                connection.close()

        thread = Thread(target=second_copy)
        with transaction.atomic():
            # The first copy, saved but not committed: invisible to `already_saved`, yet holding the token.
            WorkOrder.objects.create(created_by=self.worker, description='První', submission_token=token)
            thread.start()
            thread.join(timeout=1)
            self.assertTrue(thread.is_alive(), 'The second copy should be waiting on the unique token.')
        thread.join()

        self.assertEqual(WorkOrder.objects.get().description, 'První')
        self.assertRedirects(responses[0], reverse('transform_create'), fetch_redirect_response=False)
        self.assertIn(ALREADY_SAVED_JOB, [str(message) for message in get_messages(responses[0].wsgi_request)])
