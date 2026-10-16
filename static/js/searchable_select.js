/*
 * Type-to-filter pickers for the row sections of the job and refuelling forms.
 *
 * Progressive enhancement: each row's <select> stays in the DOM, hidden, and is
 * still what the form posts. Without this script the rows are plain selects.
 *
 * Options, labels, order and the placeholder (the empty_label) are re-read from
 * the <select> every time the list opens, so options job_rows.js hid as taken
 * stay hidden here too. A pick dispatches `change` so job_rows.js re-hides it
 * elsewhere. Rows job_rows.js adds are picked up by a MutationObserver, so
 * neither script depends on the other.
 */
(function () {
    'use strict';

    // Every <select> in a row gets a picker; each one's empty_label is a bare noun.
    var ROW_SELECT = '.item-row select';

    // Client-side only; the box posts nothing. Longer chosen labels still show whole.
    var SEARCH_MAX_LENGTH = 40;

    // Case- and diacritic-insensitive: „ster" finds „Štěrk".
    function fold(text) {
        var lower = text.toLowerCase();
        return lower.normalize ? lower.normalize('NFD').replace(/[\u0300-\u036f]/g, '') : lower;
    }

    // The pickable options: not the empty_label (that is the placeholder), and
    // not ones hidden as taken by another row, except this row's own.
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
        var baseId = select.id || 'combo-' + select.name;
        var listId = baseId + '-list';
        var placeholder = placeholderOf(select);
        var shown = [];
        var active = -1;

        wrapper.className = 'combo';
        select.parentNode.insertBefore(wrapper, select);
        wrapper.appendChild(select);

        // An id (so Chrome doesn't flag the field) but never a name, which would post it.
        input.id = baseId + '-search';
        input.type = 'text';
        input.className = 'combo-input';
        input.value = labelOf(select);
        input.placeholder = placeholder;
        input.autocomplete = 'off';
        input.maxLength = SEARCH_MAX_LENGTH;
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

        // Still submitted: `hidden` is not `disabled`.
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

        // The <li>s only mirror the <select>'s options.
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
                // Tabbing away from a half-typed box takes the highlighted option.
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
            // An emptied box blanks the row; anything else half-typed reverts.
            if (input.value.trim() === '') {
                assign('');
            }
            input.value = labelOf(select);
            close();
        });

        // Keep the box in step with whatever the form would post.
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
        // New rows are direct children, so the subtree (and every <li> here) isn't watched.
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
