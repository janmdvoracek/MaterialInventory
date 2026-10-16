"""The page contracts the job-form scripts read. The scripts themselves are untested by design."""

import re
from decimal import Decimal

from django import forms
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from workorders.forms import (
    JOB_SECTIONS,
)
from workorders.views import MAX_ROWS_PER_SECTION, MIN_ROWS_PER_SECTION

from .helpers import ReviewFixtureMixin, job_payload


class RowTemplateTests(ReviewFixtureMixin, TestCase):
    """The contract `static/js/job_rows.js` reads off the page.

    The script itself is untested (there is no browser runner), so a broken contract would fail silently.
    """

    def _page(self, user=None):
        self.client.force_login(user or self.worker)
        return self.client.get(reverse('transform_create'))

    def test_every_section_renders_a_row_template(self):
        response = self._page()
        for prefix, field in (
            ('workers', 'user'),
            ('consumed', 'material'),
            ('produced', 'material'),
            ('machines', 'machine'),
        ):
            self.assertContains(response, f'{prefix}-__prefix__-{field}')

    def test_the_template_is_inert_until_a_copy_is_inserted(self):
        """It sits inside the <form>, so if it were ordinary markup the browser
        would post `__prefix__` rows and the formset would choke on them."""
        response = self._page()
        self.assertContains(response, '<template data-row-template>')

    def test_the_row_bounds_are_rendered_rather_than_restated(self):
        """The floor and the cap the script honours are the view's own
        constants, so the two implementations cannot drift apart."""
        response = self._page()
        self.assertContains(
            response,
            f'data-job-section data-min-rows="{MIN_ROWS_PER_SECTION}" data-max-rows="{MAX_ROWS_PER_SECTION}"',
            count=len(JOB_SECTIONS),
        )

    def test_the_sum_button_names_its_source_and_field(self):
        """job_rows.js sums `data-sum-field` across the `data-sum-from` section
        into the section's own single row, as `_summed_rows` does."""
        response = self._page()
        self.assertContains(response, 'data-sum-from="produced" data-sum-field="quantity"', count=1)
        self.assertContains(response, 'data-sum-rows', count=1)

    def test_the_tonnage_mirror_names_its_source_and_field(self):
        """job_rows.js copies the `data-mirror-from` section's `data-mirror-source`
        total into each machine row's `data-mirror-field`, as `_mirrored_rows` does."""
        response = self._page()
        self.assertContains(
            response, 'data-mirror-from="consumed" data-mirror-source="quantity" data-mirror-field="tons"', count=1
        )
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reverse('refuel_create')), 'data-mirror-')

    def test_the_template_row_is_not_narrowed_by_the_hiding(self):
        """`_hide_taken_choices` touches `formset.forms` and must not reach
        `empty_form`: a row the script adds has to offer the whole catalog, and
        it is the script that hides from it live."""
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['consumed_formset']
        # The spare row is narrowed, as DuplicateRowTests asserts.
        self.assertNotIn(self.material_raw, formset.forms[1].fields['material'].queryset)
        self.assertIn(self.material_raw, formset.empty_form.fields['material'].queryset)

    def test_the_template_row_gets_the_collaborator_pair(self):
        """`empty_form` is built from the formset's `form_kwargs`, so a manager
        recording for a worker gets a template row scoped to that author — not
        one offering the author themselves."""
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('transform_create'),
            dict(
                job_payload(
                    [{'material': self.material_raw, 'quantity': Decimal('5')}],
                    [{'material': self.material_finished, 'quantity': Decimal('5')}],
                ),
                add_workers='',
                author=str(self.worker.pk),
            ),
        )
        choices = response.context['worker_formset'].empty_form.fields['user'].queryset
        self.assertNotIn(self.worker, choices)
        self.assertIn(self.manager, choices)
        self.assertIn(self.other_worker, choices)

    def test_the_template_row_honours_keep_on_the_edit_form(self):
        """Same `keep` the real rows get, so a retired record the job names
        stays reachable on a row the script adds."""
        work_order = self.submit_job(self.worker)
        self.material_raw.is_active = False
        self.material_raw.save(update_fields=['is_active'])
        self.client.force_login(self.manager)
        formset = self.client.get(reverse('job_edit', args=[work_order.pk])).context['consumed_formset']
        self.assertIn(self.material_raw, formset.empty_form.fields['material'].queryset)

    def test_both_job_pages_load_the_scripts(self):
        """They are linked from the shared partial, so neither page can lose
        one — and a page carrying job_rows.js without searchable_select.js
        would add rows whose dropdowns are the only ones you cannot search."""
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        for url in (reverse('transform_create'), reverse('job_edit', args=[work_order.pk])):
            response = self.client.get(url)
            for script in ('js/job_rows.js', 'js/searchable_select.js', 'js/photo_picker.js'):
                self.assertContains(response, script)

    def test_no_script_reaches_for_anything_off_the_server(self):
        """The htmx <script> this app used to carry pointed at unpkg.com, which
        the depot could not necessarily reach. Committed files, no CDN."""
        for name in ('job_rows.js', 'searchable_select.js', 'photo_picker.js', 'pwa.js'):
            source = (settings.BASE_DIR / 'static' / 'js' / name).read_text(encoding='utf-8')
            self.assertNotIn('http://', source, name)
            self.assertNotIn('https://', source, name)

    def test_the_stylesheet_actually_hides_a_hidden_button(self):
        """`button, .btn` sets a `display`, which beats the UA `[hidden]` rule, so app.css must hide `button[hidden]`
        explicitly.
        """
        css = (settings.BASE_DIR / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'button\[hidden\][^{]*\{[^}]*display:\s*none')


class SearchablePickerTests(ReviewFixtureMixin, TestCase):
    """The contract `static/js/searchable_select.js` reads off a row; the script itself is untested."""

    def _page(self):
        self.client.force_login(self.worker)
        return self.client.get(reverse('transform_create'))

    def test_every_row_has_exactly_one_select(self):
        """`.item-row select` is how the script finds the picker; a second
        dropdown in a row would silently get one too, over a field whose
        `empty_label` is not a placeholder."""
        for section in JOB_SECTIONS:
            pickers = [
                name for name, field in section.row_form().fields.items() if isinstance(field, forms.ModelChoiceField)
            ]
            self.assertEqual(len(pickers), 1, section.prefix)

    def test_the_first_option_is_the_empty_label(self):
        """It carries no value and is the row's only label (see
        `EmptyLabelTests`), so the script lifts it out as the text box's
        placeholder rather than offering it as something to pick."""
        response = self._page()
        for label in ('Pracovník', 'Materiál', 'Stroj'):
            self.assertContains(response, f'<option value="" selected>{label}</option>')

    def test_every_row_select_is_identified(self):
        """The listbox id is derived from the select's, so two rows on one page
        cannot end up sharing one."""
        response = self._page()
        for prefix, field in (
            ('workers', 'user'),
            ('consumed', 'material'),
            ('produced', 'material'),
            ('machines', 'machine'),
        ):
            self.assertContains(response, f'id="id_{prefix}-0-{field}"')

    def test_the_stylesheet_carries_the_picker_rules(self):
        """Without these rules the list renders inline, pushing the rows down, and the hidden select reappears."""
        css = (settings.BASE_DIR / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'\.combo\s*\{[^}]*position:\s*relative')
        self.assertRegex(css, r'\.combo-list\s*\{[^}]*position:\s*absolute')
        self.assertRegex(css, r'\.combo select\[hidden\][^{]*\{[^}]*display:\s*none')


class PhotoPickerTests(ReviewFixtureMixin, TestCase):
    """The contract `static/js/photo_picker.js` reads off the „Fotky" block; the script itself is untested.

    A mismatch fails silently, or makes a menu button submit the job.
    """

    def _pages(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        for url in (reverse('transform_create'), reverse('job_edit', args=[work_order.pk])):
            yield url, self.client.get(url).content.decode()

    def _template(self, page, name='data-photo-picker'):
        match = re.search(rf'<template {name}>(.*?)</template>', page, re.DOTALL)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_each_input_sits_in_its_source_wrapper(self):
        """The script finds the inputs as `[data-photo-source] input[type=file]`."""
        for url, page in self._pages():
            with self.subTest(url=url):
                self.assertIn('data-photo-field', page)
                self.assertRegex(
                    page,
                    r'data-photo-source="camera">\s*<label[^>]*>[^<]*</label>\s*<input type="file" name="camera_photo"',
                )
                self.assertRegex(page, r'data-photo-source="gallery">\s*<label for="id_photo"')

    def test_the_templates_carry_the_menu_both_choices_and_the_list(self):
        for url, page in self._pages():
            with self.subTest(url=url):
                template = self._template(page)
                for hook in ('data-photo-toggle', 'data-photo-menu', 'data-photo-list'):
                    self.assertIn(hook, template)
                self.assertIn('data-photo-pick="camera"', template)
                self.assertIn('data-photo-pick="gallery"', template)
                entry = self._template(page, 'data-photo-entry')
                for hook in ('data-photo-name', 'data-photo-remove'):
                    self.assertIn(hook, entry)

    def test_no_button_in_the_template_submits(self):
        """A <button> in a form is a submit unless told otherwise, and the
        picker is cloned inside the job form: „Vyfotit" posting the job half
        filled in would be the worst way for this to break."""
        for url, page in self._pages():
            with self.subTest(url=url):
                buttons = re.findall(r'<button[^>]*>', self._template(page))
                buttons += re.findall(r'<button[^>]*>', self._template(page, 'data-photo-entry'))
                self.assertEqual(len(buttons), 4)
                for button in buttons:
                    self.assertIn('type="button"', button)

    def test_the_stylesheet_hides_what_the_script_hides(self):
        """`label`, `input` and `.photo-menu` all set a `display`, which beats
        the UA stylesheet's `[hidden]`; without this rule the hidden inputs and
        the closed menu render. The browser is the only place that shows."""
        css = (settings.BASE_DIR / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'\.photo-field \[hidden\], \.photo-field input\[hidden\]\s*\{[^}]*display:\s*none')
