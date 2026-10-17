/*
 * „+ další řádek" / „− odebrat řádek" without a page load.
 *
 * Progressive enhancement only. Both buttons are real submits that the server
 * (`EntryPage`) answers by re-rendering one row bigger or smaller; this does
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
 *
 * A section with `data-mirror-from` (the machines) keeps its `data-mirror-field`
 * box (tuny) equal to the other section's `data-mirror-source` total (what was
 * consumed), on every row that names a machine. A box the user typed into is
 * left alone. The view does the same on each rebuild (`_mirrored_rows`).
 */
(function () {
    'use strict';

    var ROW_SELECT = '.item-row select';

    // One per mirroring section; each re-reads its source total.
    var mirrors = [];

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

    function sectionFor(prefix) {
        var input = document.querySelector('input[name="' + prefix + '-TOTAL_FORMS"]');
        return input ? input.closest('[data-job-section]') : null;
    }

    /* A box's value in hundredths, the boxes' own precision, so 0.1 + 0.2 stays
     * 0.3. Null when blank or unreadable. */
    function hundredths(input) {
        var value = Number(input.value);
        return input.value !== '' && Number.isFinite(value) ? Math.round(value * 100) : null;
    }

    // A section's `field` total in hundredths, or null if nothing is typed.
    function sectionTotal(section, field) {
        var total = null;
        section.querySelectorAll('.item-row input[name$="-' + field + '"]').forEach(function (input) {
            var value = hundredths(input);
            if (value !== null) {
                total = (total || 0) + value;
            }
        });
        return total;
    }

    function fromHundredths(total) {
        return total === null ? '' : String(total / 100);
    }

    /* Fill the single row's `data-sum-field` box with the total of that field
     * across the `data-sum-from` section. Nothing typed leaves the box alone. */
    function sumRows(section) {
        var field = section.dataset.sumField;
        var source = sectionFor(section.dataset.sumFrom);
        var target = section.querySelector('.item-row input[name$="-' + field + '"]');
        if (!source || !target) {
            return;
        }
        var total = sectionTotal(source, field);
        if (total !== null) {
            target.value = fromHundredths(total);
        }
    }

    function refreshMirrors() {
        mirrors.forEach(function (follow) {
            follow();
        });
    }

    /* Live version of `_mirrored_rows`. A row follows the source total while it
     * names a machine and its box is blank or still holds the last total copied
     * in; anything else was typed, and stays. Picking a machine fills a blank
     * box; clearing it empties a box that only held the total, which would
     * otherwise be refused as a number without a machine. */
    function watchMirror(section) {
        var field = section.dataset.mirrorField;
        var pickerSuffix = '-' + section.dataset.uniqueField;
        var last = currentTotal();

        function currentTotal() {
            var source = sectionFor(section.dataset.mirrorFrom);
            return source ? sectionTotal(source, section.dataset.mirrorSource) : null;
        }

        function box(row) {
            return row.querySelector('input[name$="-' + field + '"]');
        }

        function picked(row) {
            var select = row.querySelector('select[name$="' + pickerSuffix + '"]');
            return Boolean(select && select.value);
        }

        mirrors.push(function follow() {
            var total = currentTotal();
            if (total === last) {
                return;
            }
            section.querySelectorAll('.item-row').forEach(function (row) {
                var input = box(row);
                if (input && picked(row) && (input.value === '' || hundredths(input) === last)) {
                    input.value = fromHundredths(total);
                }
            });
            last = total;
        });

        section.addEventListener('change', function (event) {
            var row = event.target.closest('.item-row');
            if (!row || !event.target.matches('select[name$="' + pickerSuffix + '"]')) {
                return;
            }
            var input = box(row);
            if (!input) {
                return;
            }
            if (picked(row)) {
                if (input.value === '') {
                    input.value = fromHundredths(last);
                }
            } else if (last !== null && hundredths(input) === last) {
                input.value = '';
            }
        });
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
        refreshMirrors();
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
        refreshMirrors();
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
                refreshMirrors();
            });
        }
        section.addEventListener('change', function (event) {
            if (event.target.matches(ROW_SELECT)) {
                refreshChoices(section);
            }
        });
        // Typing in any box; a follower whose source total is unchanged does nothing.
        section.addEventListener('input', refreshMirrors);
        if (section.dataset.mirrorFrom) {
            watchMirror(section);
        }
        refreshChoices(section);
    });
})();
