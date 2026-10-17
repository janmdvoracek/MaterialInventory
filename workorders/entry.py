"""The entry page: recording or editing an entry (a job or a refuelling).

`EntryPage` builds every form on the page and answers the row buttons
(`add_<prefix>` / `remove_<prefix>`) and the sum button (`sum_<prefix>`) by
rebuilding them unbound, so the page works with every script blocked. It also
validates the page, puts up the error banner, shapes the template context, and
writes the rows, each through its section (`sections.Section`). What an entry
*is* (its own fields, which sections it has, how its own fields are saved)
is the entry kind's (`kinds.Kind`).
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages

from .forms import job_row_formset

# Applied to every section on every rebuild, so no POST can leave one empty.
MIN_ROWS_PER_SECTION = 1

# A rebuild reads TOTAL_FORMS straight from the POST, so cap it. Matches MAX_NUM_FORMS.
MAX_ROWS_PER_SECTION = 1000

# A banner at the top: field errors far down a phone screen are easy to miss.
REQUIRED_FIELDS_MESSAGE = 'Vyplňte prosím všechna povinná pole.'
FORM_ERRORS_MESSAGE = 'Formulář obsahuje chyby, opravte prosím vyznačená pole.'


class EntryPage:
    """Every form on an entry page, built from the request.

    - Row button: rebuilt *unbound* from the POST with that section resized, so
      a half-filled form comes back without errors.
    - Sum button: rebuilt the same way, with that section's single row filled
      in from its `sum_from` section (`_summed_rows`).
    - Either way, a blank `mirror_field` is filled from its `mirror_from`
      section's total (`_mirrored_rows`), as job_rows.js does live.
    - Any other POST: bound, and `submitted` is True.
    - GET: fresh forms, pre-filled from `order_initial` and each section's
      rows as recorded on `entry` (none when `entry` is None).

    `order_kwargs` go to `form_class`. `author` is whose rows these are and
    `viewer` who fills the page in; each section turns them, and its recorded
    rows, into its row-form kwargs (see `Section.row_form_kwargs`).
    """

    def __init__(self, request, *, form_class, sections, order_kwargs, author, viewer, entry=None, order_initial=None):
        self.request = request
        self.sections = sections
        self.author = author
        self.submitted = False
        # Read on every request, not just GET: the recorded rows also decide which retired records stay pickable.
        recorded = {
            section.prefix: section.recorded_rows(entry, author) if entry is not None else [] for section in sections
        }
        form_kwargs = {
            section.prefix: section.row_form_kwargs(recorded[section.prefix], author=author, viewer=viewer)
            for section in sections
        }
        post = request.POST if request.method == 'POST' else None
        pressed = _pressed_row_button(post, sections) if post is not None else None
        summed = _pressed_sum_button(post, sections) if post is not None else None
        if pressed or summed:
            self.order_form = form_class(**order_kwargs)
            # Read off the form's own fields, so „Zapsat za" is carried when present;
            # every value of a multi-value one, so a ticked „Odebrat" stays ticked.
            self.order_form.initial = {
                name: post.getlist(name)
                if getattr(field.widget, 'allow_multiple_selected', False)
                else post.get(name, '')
                for name, field in self.order_form.fields.items()
            }
            self.formsets = _rebuilt_formsets(post, sections, form_kwargs, pressed or (None, 0), summed)
        elif post is not None:
            # request.FILES for „Fotky"; the rows carry no upload of their own.
            self.order_form = form_class(post, request.FILES, **order_kwargs)
            self.formsets = {
                section.prefix: job_row_formset(section, data=post, form_kwargs=form_kwargs[section.prefix])
                for section in sections
            }
            self.submitted = True
        else:
            self.order_form = form_class(initial=order_initial, **order_kwargs)
            self.formsets = {
                section.prefix: job_row_formset(
                    section, rows=recorded[section.prefix], form_kwargs=form_kwargs[section.prefix]
                )
                for section in sections
            }

    def rows_valid(self):
        """True if every row section validates. No banner; see `is_valid`."""
        return all(formset.is_valid() for formset in self.formsets.values())

    def is_valid(self):
        """True if every form on the page validates; otherwise puts up the error banner."""
        order_valid = self.order_form.is_valid()
        if order_valid and self.rows_valid():
            return True
        messages.error(self.request, _form_error_summary(self.order_form, self.formsets))
        return False

    def filled_rows(self, prefix):
        """The rows of one section that name something in its picker, as typed. Duplicates were already refused."""
        formset = self.formsets[prefix]
        return [form.cleaned_data for form in formset if form.cleaned_data.get(formset.unique_field)]

    def write_rows(self, entry):
        """Replace every section's stored rows on `entry` with the filled rows, as `author`'s.

        Call inside `transaction.atomic()`.
        """
        for section in self.sections:
            section.write(entry, self.filled_rows(section.prefix), self.author)

    def context(self, **extra):
        """The page's context: its form, `sections` in render order, and each formset by name.

        The row bounds are rendered so `job_rows.js` reads them instead of restating them.
        """
        return {
            'order_form': self.order_form,
            'sections': [(section, self.formsets[section.prefix]) for section in self.sections],
            'min_rows': MIN_ROWS_PER_SECTION,
            'max_rows': MAX_ROWS_PER_SECTION,
            **{section.context_name: self.formsets[section.prefix] for section in self.sections},
            **extra,
        }


def _form_error_summary(order_form, formsets):
    """The banner for a refused entry page: the required-field wording if any error is `required`."""
    forms = [order_form, *(form for formset in formsets.values() for form in formset.forms)]
    if any(error.code == 'required' for form in forms for errors in form.errors.as_data().values() for error in errors):
        return REQUIRED_FIELDS_MESSAGE
    return FORM_ERRORS_MESSAGE


def _rebuilt_formsets(post_data, sections, form_kwargs, pressed, summed):
    """Every section rebuilt unbound from the POST: resized by the pressed row button, then summed, then mirrored."""
    pressed_prefix, delta = pressed
    rebuilt = {}
    for section in sections:
        rows, blank_rows = _resized_section(
            _submitted_rows(post_data, section),
            delta if section.prefix == pressed_prefix else 0,
        )
        if section is summed:
            source = next(other for other in sections if other.prefix == section.sum_from)
            rows = _summed_rows(rows, section, _submitted_rows(post_data, source))
        rebuilt[section.prefix] = rows, blank_rows
    formsets = {}
    for section in sections:
        rows, blank_rows = rebuilt[section.prefix]
        if section.mirror_from:
            # From the rebuilt rows, so a summed consumed row reaches the machines too.
            rows = _mirrored_rows(rows, section, rebuilt[section.mirror_from][0])
        formsets[section.prefix] = job_row_formset(
            section, rows=rows, blank_rows=blank_rows, form_kwargs=form_kwargs[section.prefix]
        )
    return formsets


def _pressed_row_button(post_data, sections):
    """`(prefix, +1 or -1)` for a pressed row button of `sections`, or None for a submission.

    Only the pressed submit button is posted, so its name being present is the signal.
    """
    for section in sections:
        if f'add_{section.prefix}' in post_data:
            return section.prefix, 1
    for section in sections:
        if f'remove_{section.prefix}' in post_data:
            return section.prefix, -1
    return None


def _pressed_sum_button(post_data, sections):
    """The section whose sum button (`sum_<prefix>`) was pressed, or None."""
    for section in sections:
        if section.sum_from and f'sum_{section.prefix}' in post_data:
            return section
    return None


def _rows_total(rows, field):
    """The total of `field` across raw POST `rows`, or None if none is readable.

    Unparsable values are skipped, since the rebuilt form is unbound and the
    real submission reports them.
    """
    total = None
    for row in rows:
        try:
            value = Decimal(row.get(field, '').strip())
        except InvalidOperation:
            continue
        if value.is_finite():
            total = value + (total or 0)
    return total


def _summed_rows(rows, section, source_rows):
    """`rows` with its single row's `sum_field` set to the total of `source_rows`.

    Only a section of exactly one row is filled; the button is hidden otherwise.
    Nothing typed leaves the row as it was.
    """
    if len(rows) != 1:
        return rows
    total = _rows_total(source_rows, section.sum_field)
    if total is None:
        return rows
    # 'f', or a sum like 1E+3 would come back in a form <input type="number"> refuses.
    return [{**rows[0], section.sum_field: format(total, 'f')}]


def _mirrored_rows(rows, section, source_rows):
    """`rows` with each blank `mirror_field` set to the `mirror_source` total of `source_rows`.

    Only rows that name their catalog record (a machine) are filled, since a
    number on an otherwise blank row is refused. A typed value is kept.
    """
    total = _rows_total(source_rows, section.mirror_source)
    if total is None:
        return rows
    picker = section.picker
    return [
        {**row, section.mirror_field: format(total, 'f')}
        if row.get(picker) and not row.get(section.mirror_field, '').strip()
        else row
        for row in rows
    ]


def _submitted_rows(post_data, section):
    """One section's rows as the raw POST strings, for the unbound rebuild."""
    prefix = section.prefix
    try:
        count = int(post_data.get(f'{prefix}-TOTAL_FORMS', 0))
    except (TypeError, ValueError):
        count = 0
    return [
        {name: post_data.get(f'{prefix}-{index}-{name}', '') for name in section.row_form.base_fields}
        for index in range(min(count, MAX_ROWS_PER_SECTION))
    ]


def _resized_section(rows, delta):
    """Apply a row button: one blank row more, or the last row less.

    Returns `(rows, blank_rows)`, never fewer than `MIN_ROWS_PER_SECTION` in total.
    """
    if delta > 0:
        blank_rows = 1
    else:
        if delta < 0 and len(rows) > MIN_ROWS_PER_SECTION:
            rows = rows[:-1]
        blank_rows = 0
    return rows, max(blank_rows, MIN_ROWS_PER_SECTION - len(rows))
