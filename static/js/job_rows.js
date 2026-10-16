/*
 * „+ další řádek" / „− odebrat řádek" without a page load.
 *
 * Progressive enhancement only. Both buttons are real submits that the server
 * (`_entry_forms`) answers by re-rendering one row bigger or smaller; this does
 * the same in the DOM. The tests can only see the server path, so it must keep
 * working without this file.
 *
 * A new row is the section's <template> (Django's `empty_form`) with
 * `__prefix__` replaced. The row bounds come from data attributes rendered by
 * the view.
 *
 * A section with `data-sum-from` also has „= součet výstupů" (`sum_<prefix>`),
 * which the server answers by filling the section's single row from the other
 * section's total (`_summed_rows`); this sums in the DOM instead. Both show it
 * only while the section has exactly one row.
 */
(function () {
    'use strict';

    var ROW_SELECT = '.item-row select';

    // The picker each row must choose differently (`data-unique-field`). A row
    // may carry other pickers (a refuel's „Zdroj"), which are left alone.
    function uniqueSelects(section) {
        var suffix = '-' + section.dataset.uniqueField;
        return Array.prototype.filter.call(section.querySelectorAll(ROW_SELECT), function (select) {
            return select.name.slice(-suffix.length) === suffix;
        });
    }

    function totalInput(section) {
        return section.querySelector('input[name$="-TOTAL_FORMS"]');
    }

    /* Hide options another row of this section already took (live version of
     * `_hide_taken_choices`; `clean()` stays the backstop). Server-rendered rows
     * never had the taken options in the DOM, so freeing one can't bring it back
     * there. */
    function refreshChoices(section) {
        var selects = uniqueSelects(section);
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

    // Hidden rather than removed, so it can come back. The view enforces the floor anyway.
    function refreshRemoveButton(section, total) {
        section.querySelector('[data-remove-row]').hidden = total <= Number(section.dataset.minRows);
    }

    // Mirrors the template: the sum button only makes sense for a single row.
    function refreshSumButton(section, total) {
        var button = section.querySelector('[data-sum-rows]');
        if (button) {
            button.hidden = total !== 1;
        }
    }

    /* Fill the single row's `data-sum-field` box with the total of that field
     * across the `data-sum-from` section. Summed in hundredths, the boxes' own
     * precision, so 0.1 + 0.2 stays 0.3. Nothing typed leaves the box alone. */
    function sumRows(section) {
        var field = section.dataset.sumField;
        var source = document.querySelector('input[name="' + section.dataset.sumFrom + '-TOTAL_FORMS"]');
        var target = section.querySelector('.item-row input[name$="-' + field + '"]');
        if (!source || !target) {
            return;
        }
        var hundredths = null;
        var boxes = source.closest('[data-job-section]').querySelectorAll('.item-row input[name$="-' + field + '"]');
        boxes.forEach(function (input) {
            var value = Number(input.value);
            if (input.value !== '' && Number.isFinite(value)) {
                hundredths = (hundredths || 0) + Math.round(value * 100);
            }
        });
        if (hundredths !== null) {
            target.value = String(hundredths / 100);
        }
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
        refreshSumButton(section, total + 1);
        refreshChoices(section);
        var first = row.querySelector('select, input');
        if (first) {
            first.focus();
        }
    }

    // Removes the last row, the one addRow appends (as `_resized_section` does).
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
        refreshSumButton(section, total - 1);
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
        var sumButton = section.querySelector('[data-sum-rows]');
        if (sumButton) {
            sumButton.addEventListener('click', function (event) {
                event.preventDefault();
                sumRows(section);
            });
        }
        section.addEventListener('change', function (event) {
            if (event.target.matches(ROW_SELECT)) {
                refreshChoices(section);
            }
        });
        refreshChoices(section);
    });
})();
