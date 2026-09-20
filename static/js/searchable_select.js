/*
 * Type-to-narrow pickers for the job form's four dropdowns — spolupracovníci,
 * spotřebováno, vyrobeno and stroje.
 *
 * Progressive enhancement, on the same terms as job_rows.js. The `<select>`
 * stays in the DOM, keeps its name and its value, and is what the browser
 * posts; this file only hides it behind a text box that filters its options
 * and writes the chosen one back. With the script missing, blocked or broken
 * every row is the ordinary `<select>` it has always been, so nothing the
 * suite drives through `self.client` changes and no behaviour lives only here.
 *
 * No choice is invented. The options, their labels, their order and the
 * `empty_label` that doubles as the row's placeholder all still come from
 * `workorders/forms.py` by way of the rendered `<select>`; the list is read
 * off it again every time it opens, which is also how the hiding job_rows.js
 * does for the no-duplicates rule is respected without the two scripts
 * knowing about each other. Writing the value back dispatches a `change`
 * event, so that hiding still runs when a pick is made here.
 *
 * Rows added by job_rows.js arrive as new children of the section, and a
 * MutationObserver enhances them — deliberately, rather than a call from the
 * other file, so neither script depends on the other being loaded first.
 * <template> contents are a separate document fragment and are not matched by
 * `querySelectorAll`, so the clone source is left as Django rendered it.
 */
(function () {
    'use strict';

    // Every row of every section has exactly one <select> — materiál, stroj or
    // pracovník. The rest of a row is number inputs.
    var ROW_SELECT = '.item-row select';

    /* Case- and diacritic-insensitive, because „ster" has to find „Štěrk" on a
     * keyboard whose owner is in a hurry and „SKODA" has to find „Škoda". */
    function fold(text) {
        var lower = text.toLowerCase();
        return lower.normalize ? lower.normalize('NFD').replace(/[\u0300-\u036f]/g, '') : lower;
    }

    /* The real choices, in the order forms.py put them.
     *
     * The valueless first option is the `empty_label` — it is the row's only
     * label, not something to pick, so it becomes the placeholder instead.
     * Options job_rows.js hid because another row took them stay out, except
     * this row's own, which it always keeps. */
    function choices(select) {
        return Array.prototype.filter.call(select.options, function (option) {
            return option.value !== '' && (!option.hidden || option.value === select.value);
        });
    }

    function placeholderOf(select) {
        var first = select.options[0];
        return first && first.value === '' ? first.text : '';
    }

    function labelOf(select) {
        var option = select.options[select.selectedIndex];
        return option && option.value !== '' ? option.text : '';
    }

    function combobox(select) {
        var wrapper = document.createElement('div');
        var input = document.createElement('input');
        var list = document.createElement('ul');
        var listId = (select.id || 'combo-' + select.name) + '-list';
        var placeholder = placeholderOf(select);
        var shown = [];
        var active = -1;

        wrapper.className = 'combo';
        select.parentNode.insertBefore(wrapper, select);
        wrapper.appendChild(select);

        input.type = 'text';
        input.className = 'combo-input';
        input.value = labelOf(select);
        input.placeholder = placeholder;
        input.autocomplete = 'off';
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', listId);
        input.setAttribute('aria-label', placeholder);

        list.id = listId;
        list.className = 'combo-list';
        list.hidden = true;
        list.setAttribute('role', 'listbox');

        wrapper.appendChild(input);
        wrapper.appendChild(list);

        // Still submitted — `hidden` is not `disabled`. No author rule sets a
        // `display` on select, so the UA stylesheet's `[hidden]` rule applies;
        // app.css says it again anyway, next to the one for buttons that need it.
        select.hidden = true;

        function setActive(index) {
            active = index;
            Array.prototype.forEach.call(list.children, function (item, i) {
                var on = i === index;
                item.classList.toggle('active', on);
                item.setAttribute('aria-selected', on ? 'true' : 'false');
            });
            if (index >= 0) {
                input.setAttribute('aria-activedescendant', list.children[index].id);
                list.children[index].scrollIntoView({ block: 'nearest' });
            } else {
                input.removeAttribute('aria-activedescendant');
            }
        }

        // Presentation only: the <li>s mirror the <select>'s options, which
        // stay the source of both the labels and the values.
        function render(query) {
            var folded = fold(query);
            shown = choices(select).filter(function (option) {
                return fold(option.text).indexOf(folded) !== -1;
            });
            list.textContent = '';
            shown.forEach(function (option, index) {
                var item = document.createElement('li');
                item.id = listId + '-' + index;
                item.className = 'combo-option';
                item.setAttribute('role', 'option');
                item.setAttribute('aria-selected', 'false');
                item.dataset.value = option.value;
                item.textContent = option.text;
                list.appendChild(item);
            });
            if (!shown.length) {
                var empty = document.createElement('li');
                empty.className = 'combo-empty';
                empty.textContent = 'Nic neodpovídá';
                list.appendChild(empty);
            }
            setActive(shown.length ? 0 : -1);
        }

        function open(query) {
            // Unhidden first, so the active row can be scrolled into view.
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            render(query);
        }

        function close() {
            list.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            setActive(-1);
        }

        function assign(value) {
            if (select.value === value) {
                return;
            }
            select.value = value;
            // job_rows.js listens for this to re-hide what is now taken.
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }

        function commit(index) {
            assign(shown[index].value);
            input.value = labelOf(select);
            close();
        }

        input.addEventListener('focus', function () {
            input.select();
            open('');
        });

        input.addEventListener('input', function () {
            open(input.value);
        });

        // Focus alone does not reopen a list closed with Enter or Escape.
        input.addEventListener('click', function () {
            if (list.hidden) {
                open('');
            }
        });

        input.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (list.hidden) {
                    open('');
                    return;
                }
                if (shown.length) {
                    setActive((active + (event.key === 'ArrowDown' ? 1 : shown.length - 1)) % shown.length);
                }
            } else if (event.key === 'Enter') {
                if (list.hidden) {
                    return; // Let it submit the form, like any other field.
                }
                event.preventDefault();
                if (active >= 0) {
                    commit(active);
                } else {
                    close();
                }
            } else if (event.key === 'Escape') {
                if (!list.hidden) {
                    event.preventDefault();
                }
                input.value = labelOf(select);
                close();
            } else if (event.key === 'Tab' && !list.hidden && active >= 0) {
                // Half-typed and tabbing on: take the highlighted row, which is
                // what the list has been showing as the answer all along. An
                // emptied box means the opposite — the row is being blanked —
                // and an untouched one has nothing to commit.
                if (input.value.trim() !== '' && input.value !== labelOf(select)) {
                    commit(active);
                }
            }
        });

        // Down on the list would blur the input before the click lands.
        list.addEventListener('mousedown', function (event) {
            event.preventDefault();
        });

        list.addEventListener('click', function (event) {
            var item = event.target.closest('.combo-option');
            if (item) {
                commit(Array.prototype.indexOf.call(list.children, item));
                input.focus();
            }
        });

        wrapper.addEventListener('focusout', function (event) {
            if (wrapper.contains(event.relatedTarget)) {
                return;
            }
            // An emptied box is how a row is blanked again; anything else
            // half-typed reverts, so the box never disagrees with the select.
            if (input.value.trim() === '') {
                assign('');
            }
            input.value = labelOf(select);
            close();
        });

        // Nothing else writes to the select today, but the box must never be
        // able to show something the form would not post.
        select.addEventListener('change', function () {
            input.value = labelOf(select);
        });

        return input;
    }

    function enhance(select) {
        if (select.dataset.combo) {
            return;
        }
        select.dataset.combo = 'on';
        var focused = document.activeElement === select;
        var input = combobox(select);
        if (focused) {
            // job_rows.js focuses the new row's select before this runs.
            input.focus();
        }
    }

    function enhanceAll(root) {
        Array.prototype.forEach.call(root.querySelectorAll(ROW_SELECT), enhance);
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-job-section]'), function (section) {
        enhanceAll(section);
        // job_rows.js inserts a row as a direct child of the section, so there
        // is no need to watch the subtree — and watching it would mean a record
        // for every <li> this file renders.
        new MutationObserver(function (records) {
            records.forEach(function (record) {
                Array.prototype.forEach.call(record.addedNodes, function (node) {
                    if (node.nodeType === 1) {
                        enhanceAll(node);
                    }
                });
            });
        }).observe(section, { childList: true });
    });
})();
