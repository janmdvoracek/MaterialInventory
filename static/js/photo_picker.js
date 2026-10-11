/*
 * One „Přidat fotku" button on the job form in place of two inputs, adding
 * every pick to a list instead of replacing the last one.
 *
 * The form takes its photos through two file inputs: `camera_photo`, which
 * carries `capture` and opens the camera, and `photo`, which opens the gallery
 * — Chrome and Opera on Android offer no camera from a bare `accept="image/*"`
 * input, and `capture` takes the gallery away (see WorkOrderForm in
 * workorders/forms.py). This file hides both behind one button whose menu
 * opens either.
 *
 * A file input holds one pick: open it again and the new pick replaces the
 * old. So after every pick the filled input is moved into the list under the
 * button and an empty copy of it takes its place, and the next shot lands in
 * the copy. The moved inputs keep their names, and the form reads every file
 * posted under a name, so each pick is posted. „Odebrat" beside a pick removes
 * its input, and with it the pick.
 *
 * Progressive enhancement, on the same terms as job_rows.js and
 * searchable_select.js. Every input stays in the form under its own name and
 * is what the browser posts — a hidden file input still submits its files, and
 * `click()` on it still opens the picker. With the script missing, blocked or
 * broken the page is the two labelled inputs it always was, and no view or
 * form knows this file exists.
 *
 * It builds no markup. The button, its menu, the list and a list entry are the
 * <template data-photo-picker> and <template data-photo-entry> in
 * _job_form_fields.html, so the Czech copy stays in the template with the rest
 * of the page's; the empty input is a copy of the server's own.
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
