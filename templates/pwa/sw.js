{% comment %}
The service worker, rendered by workorders/pwa.py::service_worker and served
at the site root so its scope covers every page. `version` changes whenever
anything it caches does; see that view.

It does one thing: when a page load fails for want of a network, it answers
with the offline page instead of the browser's own error. Everything else goes
to the network exactly as it would without it — no page is ever cached, no
POST is ever intercepted, nothing is queued for later. A submission made
offline fails the way it always did, and the browser's Back still has the form.
{% endcomment %}'use strict';

const CACHE = 'vyrobapk-offline-{{ version }}';
const ASSETS = {{ assets_json|safe }};
const OFFLINE_URL = ASSETS[0];

self.addEventListener('install', (event) => {
    // Without cookies, so the cached offline page is the anonymous one.
    event.waitUntil(
        caches.open(CACHE)
            .then((cache) => cache.addAll(ASSETS.map((url) => new Request(url, { credentials: 'omit' }))))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys.filter((key) => key.startsWith('vyrobapk-') && key !== CACHE).map((key) => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') {
        return;
    }
    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(() => caches.match(OFFLINE_URL, { ignoreVary: true }))
        );
        return;
    }
    // The offline page's own stylesheet and logo, and only those, fall back to
    // the cache — network first, so the cached copy is never what an online
    // page gets.
    const url = new URL(request.url);
    if (url.origin === self.location.origin && ASSETS.includes(url.pathname)) {
        event.respondWith(
            fetch(request).catch(() => caches.match(request, { ignoreVary: true }))
        );
    }
});
