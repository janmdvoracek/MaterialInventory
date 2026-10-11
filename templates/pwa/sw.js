{% comment %}
Rendered by workorders/pwa.py::service_worker. It does one thing: answer a
failed page load with the offline page. No other page is cached, no POST is
intercepted, nothing is queued.
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
    // Only the offline page's own assets fall back to the cache, network first.
    const url = new URL(request.url);
    if (url.origin === self.location.origin && ASSETS.includes(url.pathname)) {
        event.respondWith(
            fetch(request).catch(() => caches.match(request, { ignoreVary: true }))
        );
    }
});
