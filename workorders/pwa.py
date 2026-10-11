"""The installable app (VyrobaPK): manifest, service worker and offline page.

All three are public. The worker is served from the root because it only
controls pages at or below its own URL. It answers a failed page load with the
offline page and does nothing else.
"""

import hashlib
import json

from django.shortcuts import render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache


def _offline_assets():
    """The offline page and its assets. The page comes first; the worker uses it as the fallback."""
    return [reverse('offline'), static('css/app.css'), static('img/logo.png')]


def manifest(request):
    return render(request, 'pwa/manifest.webmanifest', content_type='application/manifest+json')


@never_cache
def service_worker(request):
    """The worker script, with the offline assets and a version written in.

    Browsers install a new worker only when its bytes change, so the version
    hashes the cached asset URLs and the offline page's markup.
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
    """Fetched by the worker without cookies, so the cached copy holds nothing personal."""
    return render(request, 'pwa/offline.html')
