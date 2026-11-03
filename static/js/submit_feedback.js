/*
 * „Odesílám…" on an entry form's save button while the form is on its way.
 *
 * Progressive enhancement only, and not what stops duplicates: that is the
 * server's `submission_token` (`Kind.save`), which also covers lost responses
 * and browser retries. Without this file the form saves the same, just with no
 * sign of progress.
 *
 * The button is the one carrying `data-sending-text`. Row and sum buttons have
 * a `name`, and a press that reaches the server re-renders the page rather than
 * saving, so they are left alone.
 */
(function () {
    'use strict';

    // The button comes back after this, in case the response never arrives.
    // Sending again is safe, and a dead button would leave no way to retry.
    var RETRY_AFTER_MS = 30000;

    var BUTTON = 'button[data-sending-text]';

    function restore(button) {
        if (button.dataset.idleText) {
            button.textContent = button.dataset.idleText;
            delete button.dataset.idleText;
        }
        button.disabled = false;
    }

    document.addEventListener('submit', function (event) {
        var button = event.target.querySelector(BUTTON);
        if (!button || (event.submitter && event.submitter.name)) {
            return;
        }
        if (button.dataset.idleText) {
            // Enter goes through the decoy button, which is still enabled.
            event.preventDefault();
            return;
        }
        button.dataset.idleText = button.textContent;
        button.textContent = button.dataset.sendingText;
        // Unnamed, so disabling it takes nothing out of the POST.
        button.disabled = true;
        window.setTimeout(function () {
            restore(button);
        }, RETRY_AFTER_MS);
    });

    // Back to this page from the browser's cache brings the sending state with it.
    window.addEventListener('pageshow', function () {
        Array.prototype.forEach.call(document.querySelectorAll(BUTTON), restore);
    });
}());
