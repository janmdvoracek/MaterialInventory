/*
 * Registers the service worker (see workorders/pwa.py), whose URL comes from
 * this script tag's `data-service-worker`. Loaded on every page; nothing
 * depends on it.
 */
(function () {
    'use strict';

    var script = document.currentScript;
    if (!script || !('serviceWorker' in navigator)) {
        return;
    }
    var url = script.dataset.serviceWorker;
    window.addEventListener('load', function () {
        // Fails on plain HTTP other than localhost; the site is just a site then.
        navigator.serviceWorker.register(url).catch(function () {});
    });
})();
