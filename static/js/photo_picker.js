/*
 * One „Fotka" button on the job form in place of two inputs.
 *
 * The form takes its one photo through two file inputs: `camera_photo`, which
 * carries `capture` and opens the camera, and `photo`, which opens the gallery
 * — Chrome and Opera on Android offer no camera from a bare `accept="image/*"`
 * input, and `capture` takes the gallery away (see WorkOrderForm in
 * workorders/forms.py). This file hides both behind one button whose menu
 * opens either.
 *
 * Progressive enhancement, on the same terms as job_rows.js and
 * searchable_select.js. Both inputs stay in the form under their own names
 * and are what the browser posts — a hidden file input still submits its
 * file, and `click()` on it still opens the picker. With the script missing,
 * blocked or broken the page is the two labelled inputs it always was, and no
 * view or form knows this file exists.
 *
 * It builds no markup. The button, its menu and the „Vybráno" line are the
 * <template data-photo-picker> in _job_form_fields.html, so the Czech copy
 * stays in the template with the rest of the page's.
 *
 * When one input gets a file the other is emptied, and so is the „Zrušit"
 * checkbox job_edit renders beside a stored photo: a job holds one photo, and
 * the form refuses a shot plus a picked file, or either plus „Zrušit". Here
 * that refusal can only be reached on purpose, by a browser without the
 * script, which is what it is there for.
 */
(function () {
    'use strict';

    function enhance(field) {
        var template = field.querySelector('template[data-photo-picker]');
        var camera = field.querySelector('[data-photo-source="camera"] input[type="file"]');
        var gallery = field.querySelector('[data-photo-source="gallery"] input[type="file"]');
        if (!template || !template.content || !camera || !gallery) {
            return;
        }
        var inputs = [camera, gallery];
        var sources = { camera: camera, gallery: gallery };
        // ClearableFileInput's box, on job_edit when the job has a photo.
        var clear = field.querySelector('input[type="checkbox"][name="' + gallery.name + '-clear"]');
        var galleryLabel = field.querySelector('label[for="' + gallery.id + '"]');

        var picker = template.content.firstElementChild.cloneNode(true);
        var toggle = picker.querySelector('[data-photo-toggle]');
        var menu = picker.querySelector('[data-photo-menu]');
        var chosen = picker.querySelector('[data-photo-chosen]');
        var chosenName = picker.querySelector('[data-photo-name]');
        var reset = picker.querySelector('[data-photo-reset]');

        menu.id = gallery.id + '-menu';
        toggle.setAttribute('aria-controls', menu.id);

        // The camera's wrapper holds nothing else, so it goes whole. The
        // gallery's holds the stored photo and „Zrušit" on job_edit, which have
        // to stay, so only its label and input go and the button takes the
        // input's place.
        camera.closest('[data-photo-source]').hidden = true;
        if (galleryLabel) {
            galleryLabel.hidden = true;
        }
        gallery.hidden = true;
        gallery.parentNode.insertBefore(picker, gallery.nextSibling);

        function picked() {
            for (var i = 0; i < inputs.length; i++) {
                if (inputs[i].files && inputs[i].files.length) {
                    return inputs[i].files[0];
                }
            }
            return null;
        }

        function showChosen() {
            var file = picked();
            chosenName.textContent = file ? file.name : '';
            chosen.hidden = !file;
        }

        function setOpen(open) {
            menu.hidden = !open;
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        }

        function emptyInputs(except) {
            inputs.forEach(function (input) {
                if (input !== except) {
                    input.value = '';
                }
            });
        }

        toggle.addEventListener('click', function () {
            setOpen(menu.hidden);
        });

        Array.prototype.forEach.call(menu.querySelectorAll('[data-photo-pick]'), function (button) {
            button.addEventListener('click', function () {
                setOpen(false);
                sources[button.getAttribute('data-photo-pick')].click();
            });
        });

        inputs.forEach(function (input) {
            input.addEventListener('change', function () {
                if (input.files && input.files.length) {
                    emptyInputs(input);
                    if (clear) {
                        clear.checked = false;
                    }
                }
                showChosen();
            });
        });

        reset.addEventListener('click', function () {
            emptyInputs(null);
            showChosen();
            toggle.focus();
        });

        if (clear) {
            clear.addEventListener('change', function () {
                if (clear.checked) {
                    emptyInputs(null);
                    showChosen();
                }
            });
        }

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

        // A pick survives a back/forward navigation in some browsers.
        showChosen();
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-photo-field]'), enhance);
})();
