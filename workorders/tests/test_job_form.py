"""How the job form renders: pickers, row buttons and errors."""

from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from machines.models import Machine
from materials.models import Material
from workorders.models import MachineUsage, StockMovement, WorkerHours, WorkOrder
from workorders.views import FORM_ERRORS_MESSAGE, MAX_ROWS_PER_SECTION, MIN_ROWS_PER_SECTION, REQUIRED_FIELDS_MESSAGE

from .helpers import ReviewFixtureMixin, default_location, job_payload


class EmptyLabelTests(TestCase):
    """Every `ModelChoiceField` sets `empty_label`; Django's default renders in English under `cs`."""

    DJANGO_DEFAULT = '- Select an option -'

    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        Material.objects.create(sku='SKU1', name='Steel Bar')
        Machine.objects.create(name='Crusher A')
        default_location()

    def test_pages_have_no_english_placeholder(self):
        self.client.force_login(self.manager)
        for name in (
            'transform_create',
            'refuel_create',
            'refuel_dashboard',
            'machine_dashboard',
            'material_dashboard',
            'time_worked',
            'job_dashboard',
        ):
            with self.subTest(view=name):
                self.assertNotContains(self.client.get(reverse(name)), self.DJANGO_DEFAULT)

    def test_transform_rows_prompt_in_czech(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('transform_create'))
        self.assertContains(response, 'Materiál')
        self.assertContains(response, 'Stroj')
        self.assertContains(response, 'Pracovník')
        # „Zapsat za" is a manager-only field, and this test is logged in as one.
        self.assertContains(response, 'Za sebe')
        self.assertContains(response, 'Vyberte lokaci')

    def test_filter_forms_offer_all_in_czech(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('machine_dashboard')), 'Všechny stroje')
        self.assertContains(self.client.get(reverse('material_dashboard')), 'Všechny materiály')
        self.assertContains(self.client.get(reverse('time_worked')), 'Všichni pracovníci')
        response = self.client.get(reverse('job_dashboard'))
        self.assertContains(response, 'Všichni uživatelé')
        self.assertContains(response, 'Všechny stavy')
        self.assertContains(response, 'Všechny lokace')
        self.assertContains(self.client.get(reverse('material_dashboard')), 'Všechny lokace')


class DuplicateRowTests(ReviewFixtureMixin, TestCase):
    """No section of the job form may name the same thing on two rows.

    Covers the rule (`clean()` on POST) and the server-side hiding of taken choices on unbound renders.
    """

    def _submit(self, **kwargs):
        self.client.force_login(self.worker)
        return self.client.post(reverse('transform_create'), job_payload(**kwargs))

    # -------------------------------------------------------------- the rule

    def test_the_same_material_twice_on_the_consumed_side_is_refused(self):
        response = self._submit(
            consumed_rows=[
                {'material': self.material_raw, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('3')},
            ],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_the_same_material_twice_on_the_produced_side_is_refused(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[
                {'material': self.material_finished, 'quantity': Decimal('2')},
                {'material': self.material_finished, 'quantity': Decimal('3')},
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())

    def test_the_complaint_names_the_material_and_is_czech(self):
        response = self._submit(
            consumed_rows=[
                {'material': self.material_raw, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('3')},
            ],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        self.assertContains(response, 'je v této sekci vybraný víckrát')
        self.assertContains(response, str(self.material_raw))

    def test_the_complaint_sits_on_the_repeated_row_not_the_first_one(self):
        """It names one row of several, so it has to render next to that row."""
        response = self._submit(
            consumed_rows=[
                {'material': self.material_raw, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('3')},
            ],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        formset = response.context['consumed_formset']
        self.assertEqual(formset.forms[0].non_field_errors(), [])
        self.assertEqual(len(formset.forms[1].non_field_errors()), 1)

    def test_one_material_may_still_be_consumed_and_produced_by_one_job(self):
        """The rule is per section: those are two statements about a material,
        not one typed twice."""
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(StockMovement.objects.filter(material=self.material_raw).count(), 2)

    def test_different_materials_on_one_side_are_still_fine(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[
                {'material': self.material_finished, 'quantity': Decimal('3')},
                {'material': self.material_raw, 'quantity': Decimal('2')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkOrder.objects.get().movements.count(), 3)

    def test_the_same_machine_twice_is_refused(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('1')},
                {'machine': self.machine_a, 'hours': Decimal('2')},
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'je vybraný víckrát')
        self.assertFalse(MachineUsage.objects.exists())

    def test_chained_machines_are_still_fine(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[
                {'machine': self.machine_a, 'hours': Decimal('1')},
                {'machine': self.machine_b, 'hours': Decimal('2')},
            ],
        )
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(MachineUsage.objects.count(), 2)

    def test_the_same_collaborator_twice_is_refused(self):
        response = self._submit(
            consumed_rows=[{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            worker_rows=[
                {'user': self.other_worker, 'hours': Decimal('3')},
                {'user': self.other_worker, 'hours': Decimal('1.5')},
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'je vybraný víckrát')
        self.assertFalse(WorkerHours.objects.exists())

    def test_a_duplicate_row_refuses_the_whole_job(self):
        """Same rule as the mass balance: nothing at all is written."""
        self._submit(
            consumed_rows=[
                {'material': self.material_raw, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('3')},
            ],
            produced_rows=[{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('1')}],
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('1')}],
        )
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(MachineUsage.objects.exists())
        self.assertFalse(WorkerHours.objects.exists())

    def test_a_manager_correcting_a_job_is_held_to_the_same_rule(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('job_edit', args=[work_order.pk]),
            job_payload(
                [
                    {'material': self.material_raw, 'quantity': Decimal('2')},
                    {'material': self.material_raw, 'quantity': Decimal('3')},
                ],
                [{'material': self.material_finished, 'quantity': Decimal('5')}],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'je v této sekci vybraný víckrát')
        # The job still holds what it held before the refused correction.
        self.assertEqual(work_order.movements.count(), 2)

    # ------------------------------------------------ not offered a second time

    def test_a_new_row_is_not_offered_what_another_row_took(self):
        """„+ další řádek" re-renders the page, which is when the list is built."""
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        data['add_consumed'] = ''
        response = self.client.post(reverse('transform_create'), data)
        new_row = response.context['consumed_formset'].forms[1]
        self.assertNotIn(self.material_raw, new_row.fields['material'].queryset)
        self.assertIn(self.material_finished, new_row.fields['material'].queryset)

    def test_the_row_that_took_it_keeps_it(self):
        """Otherwise the option it has to re-select would not be in its own list,
        and the row would come back blank."""
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        data['add_consumed'] = ''
        response = self.client.post(reverse('transform_create'), data)
        first_row = response.context['consumed_formset'].forms[0]
        self.assertIn(self.material_raw, first_row.fields['material'].queryset)
        self.assertContains(response, f'value="{self.material_raw.pk}" selected')

    def test_the_two_material_sections_do_not_hide_from_each_other(self):
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        data['add_produced'] = ''
        response = self.client.post(reverse('transform_create'), data)
        new_row = response.context['produced_formset'].forms[1]
        self.assertIn(self.material_raw, new_row.fields['material'].queryset)

    def test_a_new_machine_row_is_not_offered_the_machine_already_running(self):
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2')}],
        )
        data['add_machines'] = ''
        response = self.client.post(reverse('transform_create'), data)
        new_row = response.context['machine_formset'].forms[1]
        self.assertNotIn(self.machine_a, new_row.fields['machine'].queryset)
        self.assertIn(self.machine_b, new_row.fields['machine'].queryset)

    def test_a_new_worker_row_is_not_offered_someone_already_named(self):
        self.client.force_login(self.worker)
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            worker_rows=[{'user': self.other_worker, 'hours': Decimal('2')}],
        )
        data['add_workers'] = ''
        response = self.client.post(reverse('transform_create'), data)
        new_row = response.context['worker_formset'].forms[1]
        self.assertNotIn(self.other_worker, new_row.fields['user'].queryset)

    def test_the_edit_forms_blank_row_hides_what_the_job_already_uses(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_edit', args=[work_order.pk]))
        formset = response.context['consumed_formset']
        self.assertIn(self.material_raw, formset.forms[0].fields['material'].queryset)
        self.assertNotIn(self.material_raw, formset.forms[1].fields['material'].queryset)

    def test_a_fresh_form_hides_nothing(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse('transform_create'))
        choices = response.context['consumed_formset'].forms[0].fields['material'].queryset
        self.assertIn(self.material_raw, choices)
        self.assertIn(self.material_finished, choices)


class RowButtonTests(ReviewFixtureMixin, TestCase):
    """„+ další řádek" / „− odebrat řádek" resize one section through a server round-trip.

    The rebuilt page is unbound: it keeps what was typed and shows no errors.
    """

    def _press(self, button, url=None, consumed=None, produced=None, **overrides):
        """Press one row button on an otherwise ordinary, balanced form."""
        data = job_payload(
            consumed or [{'material': self.material_raw, 'quantity': Decimal('5')}],
            produced or [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        data.update(overrides)
        data[button] = ''
        return self.client.post(url or reverse('transform_create'), data)

    def _three_consumed(self):
        """Three consumed rows with quantities that cannot be confused for anything
        else on the page — the hours field and the produced row both hold small
        numbers."""
        return [{'material': self.material_raw, 'quantity': q} for q in ('11', '22', '33')]

    # ---------------------------------------------------------------- adding

    def test_the_named_section_gains_a_row_and_the_others_do_not(self):
        self.client.force_login(self.worker)
        response = self._press('add_consumed')
        # The typed row plus the blank one just added; every other section keeps
        # the single row `job_payload` sends and gains nothing.
        self.assertEqual(len(response.context['consumed_formset'].forms), 2)
        self.assertEqual(len(response.context['produced_formset'].forms), 1)
        self.assertEqual(len(response.context['machine_formset'].forms), 1)
        self.assertEqual(len(response.context['worker_formset'].forms), 1)

    def test_the_new_row_is_reachable_in_the_rendered_form(self):
        """The point of the whole feature: a fourth row the browser can post."""
        self.client.force_login(self.worker)
        # Three different fractions: a section refuses the same material twice,
        # which is the shape a real four-row job has anyway.
        third = Material.objects.create(sku='FIN2', name='Frakce 16/32')
        data = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [
                {'material': self.material_finished, 'quantity': Decimal('2')},
                {'material': third, 'quantity': Decimal('2')},
                {'material': self.material_raw, 'quantity': Decimal('1')},
            ],
        )
        data['add_produced'] = ''
        response = self.client.post(reverse('transform_create'), data)
        self.assertContains(response, 'produced-3-material')
        self.assertContains(response, 'produced-3-quantity')
        self.assertEqual(response.context['produced_formset'].management_form['TOTAL_FORMS'].value(), 4)

    def test_what_was_typed_comes_back(self):
        self.client.force_login(self.worker)
        response = self._press('add_produced', description='Drcení na frakce', hours='7')
        self.assertContains(response, 'value="Drcení na frakce"')
        self.assertContains(response, 'value="7"')
        # The material already chosen is re-selected, not reset to the prompt.
        self.assertContains(response, f'value="{self.material_raw.pk}" selected')

    def test_the_date_comes_back_as_iso_rather_than_czech(self):
        """`<input type="date">` reads only ISO; a regression shows as a silently blank date box."""
        self.client.force_login(self.worker)
        today = timezone.localdate().isoformat()
        response = self._press('add_machines', performed_on=today)
        self.assertContains(response, f'value="{today}"')

    def test_nothing_is_written(self):
        self.client.force_login(self.worker)
        self._press('add_consumed')
        self.assertEqual(WorkOrder.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_an_unfinished_form_is_not_scolded(self):
        """A bound rebuild would put „Toto pole je vyžadováno." over the hours
        box of somebody who has not reached it yet."""
        self.client.force_login(self.worker)
        response = self._press('add_consumed', hours='', description='')
        self.assertNotContains(response, 'Toto pole je vyžadováno.')
        self.assertNotContains(response, 'Přidejte alespoň jednu položku')
        self.assertEqual(list(response.context['messages']), [])

    def test_an_unbalanced_form_is_not_scolded_either(self):
        self.client.force_login(self.worker)
        response = self._press('add_produced', **{'produced-0-quantity': '9'})
        self.assertNotContains(response, 'se musí rovnat')
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_the_collaborator_list_follows_zapsat_za(self):
        """The author decides who may be named, so the rebuild must resolve „Zapsat za" first."""
        self.client.force_login(self.manager)
        response = self._press('add_workers', author=str(self.worker.pk))
        choices = response.context['worker_formset'].forms[0].fields['user'].queryset
        self.assertNotIn(self.worker, choices)
        self.assertIn(self.manager, choices)
        self.assertIn(self.other_worker, choices)
        self.assertContains(response, f'value="{self.worker.pk}" selected')

    def test_a_worker_growing_their_own_form_keeps_the_workers_only_list(self):
        self.client.force_login(self.worker)
        response = self._press('add_workers')
        choices = response.context['worker_formset'].forms[0].fields['user'].queryset
        self.assertNotIn(self.worker, choices)
        self.assertNotIn(self.manager, choices)
        self.assertIn(self.other_worker, choices)

    def test_a_tampered_row_count_does_not_blow_up(self):
        """TOTAL_FORMS is read straight out of the POST here, so it is untrusted."""
        self.client.force_login(self.worker)
        response = self._press('add_consumed', **{'consumed-TOTAL_FORMS': 'není číslo'})
        self.assertEqual(response.status_code, 200)
        # Nothing to restore, so the section comes back as its new blank row.
        self.assertEqual(len(response.context['consumed_formset'].forms), 1)

    def test_an_absurd_row_count_is_capped(self):
        self.client.force_login(self.worker)
        response = self._press('add_consumed', **{'consumed-TOTAL_FORMS': '999999'})
        # Exactly the cap, not the cap plus the new blank row: at the ceiling
        # Django will not add an extra beyond its own max_num, which is the same
        # 1000. Nothing legitimate reaches this, and a form that did would be
        # refused by the formset on submit anyway.
        self.assertEqual(len(response.context['consumed_formset'].forms), MAX_ROWS_PER_SECTION)

    def test_job_edit_grows_the_same_way(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self._press('add_machines', url=reverse('job_edit', args=[work_order.pk]))
        self.assertEqual(len(response.context['machine_formset'].forms), 2)
        self.assertEqual(len(response.context['consumed_formset'].forms), 1)

    def test_job_edit_saves_nothing_while_growing(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        self._press('add_consumed', url=reverse('job_edit', args=[work_order.pk]), description='Nemá se uložit')
        work_order.refresh_from_db()
        self.assertNotEqual(work_order.description, 'Nemá se uložit')

    def test_job_edit_keeps_the_authors_name_on_the_hours_label(self):
        """The label override applies on every branch, the grown one included."""
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self._press('add_workers', url=reverse('job_edit', args=[work_order.pk]))
        self.assertEqual(response.context['order_form'].fields['hours'].label, f'Hodiny – {self.worker.username}')

    def test_enter_still_saves_rather_than_adding_a_row(self):
        """Enter submits through the first submit button, so a decoy must precede „+ další řádek"."""
        self.client.force_login(self.worker)
        body = self.client.get(reverse('transform_create')).content.decode()
        self.assertLess(body.index('visually-hidden'), body.index('name="add_workers"'))

    # -------------------------------------------------------------- removing

    def test_the_named_section_loses_a_row_and_the_others_do_not(self):
        self.client.force_login(self.worker)
        response = self._press('remove_consumed', consumed=self._three_consumed())
        self.assertEqual(len(response.context['consumed_formset'].forms), 2)
        self.assertEqual(len(response.context['produced_formset'].forms), 1)
        self.assertEqual(len(response.context['machine_formset'].forms), 1)
        self.assertEqual(len(response.context['worker_formset'].forms), 1)

    def test_the_row_that_goes_is_the_last_one(self):
        """The exact row „+ další řádek" would have added, so the two buttons
        undo each other."""
        self.client.force_login(self.worker)
        response = self._press('remove_consumed', consumed=self._three_consumed())
        self.assertContains(response, 'value="11"')
        self.assertContains(response, 'value="22"')
        self.assertNotContains(response, 'value="33"')

    def test_the_last_row_is_never_removed(self):
        """A section with one row left stays at one, however often it is pressed.

        The button is hidden at that point, so this is the POST arriving anyway —
        a double tap on a slow connection, or a hand-edited form.
        """
        self.client.force_login(self.worker)
        response = self._press('remove_consumed')
        self.assertEqual(len(response.context['consumed_formset'].forms), MIN_ROWS_PER_SECTION)

    def test_the_remove_button_is_hidden_on_a_section_down_to_one_row(self):
        """Rendered `hidden`, not omitted, so `job_rows.js` can unhide it. The view enforces the floor itself."""
        self.client.force_login(self.worker)
        # Two machine rows, so that section still has one to spare and its own
        # button stays up — hiding is per section, not per page.
        response = self._press('remove_consumed', **{'machines-TOTAL_FORMS': '2'})
        self.assertContains(response, 'name="remove_consumed" data-remove-row formnovalidate hidden')
        # The way back is still offered, and the roomier section keeps both.
        self.assertContains(response, 'name="add_consumed"')
        self.assertContains(response, 'name="remove_machines" data-remove-row formnovalidate>')

    def test_no_section_ever_comes_back_empty(self):
        """The floor holds for the sections nobody pressed, too — otherwise a
        hand-edited TOTAL_FORMS would leave a bare heading with no row under it."""
        self.client.force_login(self.worker)
        response = self._press('add_consumed', **{'produced-TOTAL_FORMS': '0'})
        self.assertEqual(len(response.context['produced_formset'].forms), MIN_ROWS_PER_SECTION)

    def test_removing_writes_nothing_and_scolds_nobody(self):
        self.client.force_login(self.worker)
        response = self._press('remove_produced', consumed=self._three_consumed(), hours='')
        self.assertNotContains(response, 'Toto pole je vyžadováno.')
        self.assertNotContains(response, 'se musí rovnat')
        self.assertEqual(WorkOrder.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_removing_keeps_the_rest_of_the_form(self):
        self.client.force_login(self.worker)
        response = self._press(
            'remove_machines',
            consumed=self._three_consumed(),
            description='Drcení na frakce',
        )
        self.assertContains(response, 'value="Drcení na frakce"')
        self.assertContains(response, f'value="{self.material_raw.pk}" selected')
        self.assertContains(response, f'value="{timezone.localdate().isoformat()}"')

    def test_job_edit_removes_the_same_way(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self._press(
            'remove_consumed',
            url=reverse('job_edit', args=[work_order.pk]),
            consumed=self._three_consumed(),
        )
        self.assertEqual(len(response.context['consumed_formset'].forms), 2)
        work_order.refresh_from_db()
        # The job still has the one line item it was recorded with.
        self.assertEqual(work_order.movements.count(), 2)


class RowErrorVisibilityTests(ReviewFixtureMixin, TestCase):
    """Nothing is written unless every form validates, so an error with nowhere
    to render is a submission that vanishes without a word."""

    def _submit(self, **overrides):
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
            machine_rows=[{'machine': self.machine_a, 'hours': Decimal('2'), 'tons': Decimal('5')}],
        )
        payload.update(overrides)
        self.client.force_login(self.worker)
        return self.client.post(reverse('transform_create'), payload)

    def test_a_bad_quantity_says_what_is_wrong_with_it(self):
        response = self._submit(**{'consumed-0-quantity': '5.123'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'desetinná místa')

    def test_a_field_error_does_not_also_claim_the_row_is_half_empty(self):
        # The row *was* filled in; the value simply did not validate. The old
        # behaviour hid the real message and printed this one instead.
        response = self._submit(**{'consumed-0-quantity': '5.123'})
        self.assertNotContains(response, 'Vyplňte materiál i množství')

    def test_motohodiny_off_the_step_say_so(self):
        response = self._submit(**{'machines-0-hours': '0.3'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Stav motohodin:')

    def test_a_genuinely_half_filled_row_still_says_so(self):
        # The guard must not swallow the rule it sits in front of: a row with a
        # material and no quantity has no field error of its own.
        response = self._submit(**{'consumed-0-quantity': ''})
        self.assertContains(response, 'Vyplňte materiál i množství')

    def test_row_errors_render_on_the_edit_form_too(self):
        # Both pages share `_job_form_fields.html`, so this cannot regress on
        # one of them alone.
        job = self.submit_job(self.worker)
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        payload['consumed-0-quantity'] = '5.123'
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_edit', args=[job.pk]), payload)
        self.assertContains(response, 'desetinná místa')


class ErrorBannerTests(ReviewFixtureMixin, TestCase):
    """A refused job also gets a red banner at the top, since field errors may be far down a phone screen."""

    def _payload(self, **overrides):
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        payload.update(overrides)
        return payload

    def _submit(self, url=None, **overrides):
        self.client.force_login(self.worker)
        return self.client.post(url or reverse('transform_create'), self._payload(**overrides))

    def _banners(self, response):
        return [str(message) for message in response.context['messages']]

    def test_a_missing_work_field_raises_the_required_banner(self):
        # „Popis" is required by the view, not the form field (see WORK_FIELDS).
        response = self._submit(description='')
        self.assertFalse(WorkOrder.objects.exists())
        self.assertEqual(self._banners(response), [REQUIRED_FIELDS_MESSAGE])
        self.assertContains(response, f'<li class="error">{REQUIRED_FIELDS_MESSAGE}</li>', html=True)

    def test_a_missing_form_level_required_field_raises_it_too(self):
        # The date is required by the form itself, so this fails before the rows are read.
        response = self._submit(performed_on='')
        self.assertFalse(WorkOrder.objects.exists())
        self.assertEqual(self._banners(response), [REQUIRED_FIELDS_MESSAGE])

    def test_several_missing_fields_raise_one_banner(self):
        response = self._submit(description='', hours='', location='')
        self.assertEqual(self._banners(response), [REQUIRED_FIELDS_MESSAGE])

    def test_a_field_that_is_filled_in_wrongly_gets_the_general_banner(self):
        response = self._submit(**{'consumed-0-quantity': '5.123'})
        self.assertFalse(WorkOrder.objects.exists())
        self.assertEqual(self._banners(response), [FORM_ERRORS_MESSAGE])

    def test_a_half_filled_row_gets_the_general_banner(self):
        response = self._submit(**{'consumed-0-quantity': ''})
        self.assertEqual(self._banners(response), [FORM_ERRORS_MESSAGE])

    def test_a_balance_error_alone_raises_no_second_banner(self):
        # Its own message is already the banner; nothing is wrong with a field.
        response = self._submit(**{'produced-0-quantity': '4'})
        self.assertFalse(WorkOrder.objects.exists())
        self.assertEqual(len(self._banners(response)), 1)
        self.assertNotIn(REQUIRED_FIELDS_MESSAGE, self._banners(response))
        self.assertNotIn(FORM_ERRORS_MESSAGE, self._banners(response))

    def test_a_missing_field_and_a_balance_error_both_show(self):
        banners = self._banners(self._submit(description='', **{'produced-0-quantity': '4'}))
        self.assertEqual(banners[0], REQUIRED_FIELDS_MESSAGE)
        self.assertEqual(len(banners), 2)

    def test_the_edit_page_raises_it_too(self):
        job = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.post(reverse('job_edit', args=[job.pk]), self._payload(description=''))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._banners(response), [REQUIRED_FIELDS_MESSAGE])

    def test_a_row_button_is_not_a_submission_and_raises_nothing(self):
        # Asking for a row on a half-filled form must not scold anyone.
        response = self._submit(description='', add_workers='')
        self.assertEqual(self._banners(response), [])

    def test_a_valid_job_raises_no_error_banner(self):
        response = self._submit()
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual([m.level_tag for m in get_messages(response.wsgi_request)], ['success'])
