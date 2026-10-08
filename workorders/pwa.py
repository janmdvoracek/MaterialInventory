"""What makes the app installable on a phone's home screen as VyrobaPK.

Three public URLs and nothing else: the web app manifest, the service worker
and the page the service worker shows when there is no signal. None of them is
behind a login — the browser fetches the manifest without cookies, and the
install prompt is as useful on the login page as anywhere.

The service worker is served from the site root rather than from /static/,
because a worker only controls pages at or below its own URL. It never caches a
page anyone is logged in to and never touches a POST: it answers a failed page
load with the offline page and nothing more, so with it missing, unregistered
or broken the app behaves exactly as it does in a browser tab.
"""

import hashlib
import json

from django.shortcuts import render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache


def _offline_assets():
    """What the offline page needs to render without a network — the page
    itself first, which is what the worker reads as its fallback."""
    return [reverse('offline'), static('css/app.css'), static('img/logo.png')]


def manifest(request):
    return render(request, 'pwa/manifest.webmanifest', content_type='application/manifest+json')


@never_cache
def service_worker(request):
    """The worker script, with the offline assets and a version written in.

    A browser installs a new worker only when the script's bytes change, so the
    version is a hash of what the worker caches: the asset URLs (hashed by the
    manifest storage in production, so a stylesheet change moves them) and the
    offline page's own markup, which no URL would otherwise reflect. Editing
    either therefore reaches installed phones on their next visit.

    `never_cache` keeps an intermediate cache from holding a stale worker; the
    browser already bypasses its own HTTP cache when it checks for updates.
    """
    assets = _offline_assets()
    offline_markup = render_to_string('pwa/offline.html')
    version = hashlib.sha256('\n'.join([*assets, offline_markup]).encode()).hexdigest()[:12]
    return render(
        request,
        'pwa/sw.js',
        # JSON rather than a template loop: these are JS string literals.
        {'assets_json': json.dumps(assets), 'version': version},
        content_type='text/javascript; charset=utf-8',
    )


def offline(request):
    """Rendered for the worker's cache, which fetches it without cookies, so
    the copy it keeps carries no nav, no user and no CSRF token."""
    return render(request, 'pwa/offline.html')
