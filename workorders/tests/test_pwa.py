"""The installable app."""

import json
import re

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .helpers import ReviewFixtureMixin


class PwaTests(ReviewFixtureMixin, TestCase):
    """Installing the app as VyrobaPK: the manifest, the service worker and the
    offline page it falls back to (workorders/pwa.py)."""

    def get_manifest(self):
        response = self.client.get(reverse('web_manifest'))
        self.assertEqual(response.status_code, 200)
        return response, json.loads(response.content)

    def test_manifest_is_public_and_valid_json(self):
        """The browser fetches the manifest without cookies, so a login
        redirect here would make the app uninstallable."""
        response, manifest = self.get_manifest()
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        self.assertEqual(manifest['name'], 'VyrobaPK')
        self.assertEqual(manifest['short_name'], 'VyrobaPK')
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['start_url'], reverse('transform_create'))
        self.assertEqual(manifest['scope'], '/')

    def test_manifest_icons_exist_at_the_sizes_they_claim(self):
        """Chrome will not offer the install without a 192 and a 512 px icon,
        and a size that lies is an icon it ignores."""
        _, manifest = self.get_manifest()
        purposes = set()
        for icon in manifest['icons']:
            name = icon['src'].removeprefix(settings.STATIC_URL)
            with Image.open(settings.BASE_DIR / 'static' / name) as image:
                self.assertEqual(f'{image.width}x{image.height}', icon['sizes'], name)
            purposes.add((icon['sizes'], icon['purpose']))
        self.assertLessEqual({('192x192', 'any'), ('512x512', 'any'), ('512x512', 'maskable')}, purposes)

    def test_manifest_theme_colour_matches_the_light_meta(self):
        """Kept in step by hand, like the metas and --chrome-bg."""
        _, manifest = self.get_manifest()
        page = self.client.get(reverse('login')).content.decode()
        light = re.search(r'name="theme-color" content="([^"]+)" media="\(prefers-color-scheme: light\)"', page)
        self.assertEqual(manifest['theme_color'], light.group(1))

    def test_every_page_links_the_manifest_and_registers_the_worker(self):
        """The login page included: it is where a new phone first lands."""
        pages = [self.client.get(reverse('login'))]
        self.client.force_login(self.worker)
        pages.append(self.client.get(reverse('transform_create')))
        for response in pages:
            self.assertContains(response, f'<link rel="manifest" href="{reverse("web_manifest")}">', html=False)
            self.assertContains(response, 'js/pwa.js')
            self.assertContains(response, f'data-service-worker="{reverse("service_worker")}"')
            self.assertContains(response, '<meta name="apple-mobile-web-app-title" content="VyrobaPK">')

    def test_worker_is_public_javascript_at_the_root(self):
        """A worker controls only pages at or below its own URL."""
        self.assertEqual(reverse('service_worker'), '/sw.js')
        response = self.client.get(reverse('service_worker'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/javascript'))
        self.assertIn('no-cache', response['Cache-Control'])

    def test_worker_caches_the_offline_page_and_its_assets_only(self):
        source = self.client.get(reverse('service_worker')).content.decode()
        assets = json.loads(re.search(r'const ASSETS = (\[.*?\]);', source).group(1))
        self.assertEqual(assets[0], reverse('offline'))
        self.assertEqual(set(assets[1:]), {'/static/css/app.css', '/static/img/logo.png'})
        self.assertIn("credentials: 'omit'", source)

    def test_worker_leaves_everything_but_a_get_alone(self):
        """Nothing is queued or replayed: a POST made offline fails the way it
        always did, rather than vanishing into a worker the suite cannot see."""
        source = self.client.get(reverse('service_worker')).content.decode()
        self.assertRegex(source, r"if \(request\.method !== 'GET'\) \{\s*return;")

    def test_worker_version_follows_the_offline_page(self):
        """A browser installs a new worker only when its bytes change, so the
        version has to move when what it caches does."""
        source = self.client.get(reverse('service_worker')).content.decode()
        version = re.search(r'vyrobapk-offline-([0-9a-f]{12})', source).group(1)
        with override_settings(STATIC_URL='/assets/'):
            moved = self.client.get(reverse('service_worker')).content.decode()
        self.assertNotIn(version, moved)

    def test_worker_reaches_for_nothing_off_the_server(self):
        source = self.client.get(reverse('service_worker')).content.decode()
        self.assertNotIn('http://', source)
        self.assertNotIn('https://', source)

    def test_offline_page_is_public_and_carries_nothing_personal(self):
        """The worker fetches it without cookies, and this is the copy it keeps:
        shown to whoever opens the app offline, so no nav and no CSRF token."""
        response = self.client.get(reverse('offline'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bez připojení')
        self.assertNotContains(response, 'csrfmiddlewaretoken')
        self.assertNotContains(response, 'class="bottom"')
