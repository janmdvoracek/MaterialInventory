/*
 * Registers the service worker that makes the app installable as VyrobaPK.
 *
 * Loaded on every page from base.html. The worker's URL is read off this
 * script tag's `data-service-worker`, which base.html fills in with {% url %},
 * so nothing here names a path. The worker itself is a template served at the
 * site root (see workorders/pwa.py); all it does is show an offline page when
 * a page load fails. With this file missing, blocked or broken the app is the
 * same website in a browser tab, and nothing else on any page depends on it.
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
