/*
 * „+ další řádek" / „− odebrat řádek" without a page load.
 *
 * Progressive enhancement, and strictly that. Both buttons are ordinary
 * submits posting `add_<prefix>` / `remove_<prefix>`, answered by `_job_forms`
 * in workorders/views.py, which rebuilds the page unbound one row bigger or
 * smaller. This file intercepts the click and does the same thing in the DOM
 * instead. With it missing, blocked or broken, the form still resizes — which
 * is not a courtesy but the arrangement the tests rest on: the suite drives
 * views through `self.client` and never runs a browser, so the server path is
 * the only one it can see and has to stay the source of truth.
 *
 * No markup is built here. Each section renders Django's own `empty_form` into
 * a <template>, so every widget, placeholder, option list and `empty_label`
 * still comes from workorders/forms.py; a new row is that template with
 * `__prefix__` swapped for the next index. The row bounds arrive as data
 * attributes off MIN_ROWS_PER_SECTION / MAX_ROWS_PER_SECTION, so the floor and
 * the cap cannot drift from the ones the view enforces.
 */
(function () {
    'use strict';

    // Every section has exactly one <select> per row — materiál, stroj or
    // pracovník. The rest of a row is number inputs.
    var ROW_SELECT = '.item-row select';

    function totalInput(section) {
        return section.querySelector('input[name$="-TOTAL_FORMS"]');
    }

    /* Hide what another row of this section already took.
     *
     * `UniqueChoiceFormSet._hide_taken_choices` does the same server-side, but
     * only as of the last render; running it on every change is most of why
     * having a script is worth it. Either way `clean()` is the backstop, so a
     * duplicate that slips past this is still refused with the real message.
     *
     * One thing this cannot undo: on a server-rendered row the taken options
     * were excluded from the queryset, so they are not in the DOM at all and
     * freeing one up again cannot bring them back. Rows added here carry the
     * full list and do behave that way.
     */
    function refreshChoices(section) {
        var selects = Array.prototype.slice.call(section.querySelectorAll(ROW_SELECT));
        var taken = {};
        selects.forEach(function (select) {
            if (select.value) {
                taken[select.value] = true;
            }
        });
        selects.forEach(function (select) {
            Array.prototype.forEach.call(select.options, function (option) {
                // A row always keeps its own choice, or it would render blank.
                var clash = Boolean(option.value) && option.value !== select.value && taken[option.value] === true;
                option.hidden = clash;
                option.disabled = clash;
            });
        });
    }

    // Hidden rather than removed, so it can come back. The view applies the
    // same floor regardless; this is presentation.
    function refreshRemoveButton(section, total) {
        section.querySelector('[data-remove-row]').hidden = total <= Number(section.dataset.minRows);
    }

    function addRow(section) {
        var input = totalInput(section);
        var total = Number(input.value);
        if (total >= Number(section.dataset.maxRows)) {
            return;
        }
        var holder = document.createElement('div');
        holder.innerHTML = section.querySelector('[data-row-template]').innerHTML.replace(/__prefix__/g, total);
        var row = holder.firstElementChild;
        section.querySelector('.row-controls').before(row);
        input.value = total + 1;
        refreshRemoveButton(section, total + 1);
        refreshChoices(section);
        var first = row.querySelector('select, input');
        if (first) {
            first.focus();
        }
    }

    // The last row, which is the exact one addRow appended, so the two buttons
    // undo each other — here and in `_resized_section`.
    function removeRow(section) {
        var input = totalInput(section);
        var total = Number(input.value);
        if (total <= Number(section.dataset.minRows)) {
            return;
        }
        var rows = section.querySelectorAll('.row-block');
        rows[rows.length - 1].remove();
        input.value = total - 1;
        refreshRemoveButton(section, total - 1);
        refreshChoices(section);
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-job-section]'), function (section) {
        section.querySelector('[data-add-row]').addEventListener('click', function (event) {
            event.preventDefault();
            addRow(section);
        });
        section.querySelector('[data-remove-row]').addEventListener('click', function (event) {
            event.preventDefault();
            removeRow(section);
        });
        section.addEventListener('change', function (event) {
            if (event.target.matches(ROW_SELECT)) {
                refreshChoices(section);
            }
        });
        refreshChoices(section);
    });
})();
