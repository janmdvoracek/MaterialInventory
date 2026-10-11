/*
 * One „Přidat fotku" button in place of the two photo inputs (camera and
 * gallery), keeping every pick instead of replacing the last.
 *
 * A file input holds one pick, so after each pick the filled input moves,
 * hidden, into a list entry, and an empty clone takes its place. Moved inputs
 * keep their names and are all posted. „Odebrat" removes an entry with its input.
 *
 * Progressive enhancement: without this file the page shows the two inputs.
 * The button, menu and entry markup come from the <template data-photo-picker>
 * and <template data-photo-entry> in _job_form_fields.html.
 */
(function () {
    'use strict';

    function enhance(field) {
        var pickerTemplate = field.querySelector('template[data-photo-picker]');
        var entryTemplate = field.querySelector('template[data-photo-entry]');
        var cameraWrapper = field.querySelector('[data-photo-source="camera"]');
        var galleryWrapper = field.querySelector('[data-photo-source="gallery"]');
        if (!pickerTemplate || !pickerTemplate.content || !entryTemplate || !entryTemplate.content
                || !cameraWrapper || !galleryWrapper) {
            return;
        }
        // The empty input each menu choice opens; replaced after every pick.
        var current = {
            camera: cameraWrapper.querySelector('input[type="file"]'),
            gallery: galleryWrapper.querySelector('input[type="file"]')
        };
        if (!current.camera || !current.gallery) {
            return;
        }

        var picker = pickerTemplate.content.firstElementChild.cloneNode(true);
        var toggle = picker.querySelector('[data-photo-toggle]');
        var menu = picker.querySelector('[data-photo-menu]');
        var list = picker.querySelector('[data-photo-list]');

        menu.id = current.gallery.id + '-menu';
        toggle.setAttribute('aria-controls', menu.id);

        cameraWrapper.hidden = true;
        galleryWrapper.hidden = true;
        galleryWrapper.parentNode.insertBefore(picker, galleryWrapper.nextSibling);

        function names(files) {
            var result = [];
            for (var i = 0; i < files.length; i++) {
                result.push(files[i].name);
            }
            return result.join(', ');
        }

        function setOpen(open) {
            menu.hidden = !open;
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        }

        // Move a filled input into the list and put an empty copy in its place.
        function keep(source) {
            var input = current[source];
            var fresh = input.cloneNode(false);
            // A clone may carry the files over in some browsers; this one must not.
            fresh.value = '';
            input.parentNode.replaceChild(fresh, input);
            current[source] = fresh;
            watch(source);

            // The label's `for` belongs to the empty copy now.
            input.removeAttribute('id');
            input.hidden = true;
            var entry = entryTemplate.content.firstElementChild.cloneNode(true);
            entry.querySelector('[data-photo-name]').textContent = names(input.files);
            entry.appendChild(input);
            entry.querySelector('[data-photo-remove]').addEventListener('click', function () {
                list.removeChild(entry);
                toggle.focus();
            });
            list.appendChild(entry);
        }

        function watch(source) {
            var input = current[source];
            input.addEventListener('change', function () {
                if (input === current[source] && input.files && input.files.length) {
                    keep(source);
                }
            });
        }

        toggle.addEventListener('click', function () {
            setOpen(menu.hidden);
        });

        Array.prototype.forEach.call(menu.querySelectorAll('[data-photo-pick]'), function (button) {
            button.addEventListener('click', function () {
                setOpen(false);
                current[button.getAttribute('data-photo-pick')].click();
            });
        });

        document.addEventListener('click', function (event) {
            if (!picker.contains(event.target)) {
                setOpen(false);
            }
        });

        picker.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && !menu.hidden) {
                setOpen(false);
                toggle.focus();
            }
        });

        ['camera', 'gallery'].forEach(function (source) {
            watch(source);
            // A pick survives a back/forward navigation in some browsers.
            if (current[source].files && current[source].files.length) {
                keep(source);
            }
        });
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-photo-field]'), enhance);
})();
