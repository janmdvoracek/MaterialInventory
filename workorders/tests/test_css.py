"""Rules read straight out of app.css."""

import re

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from accounts.models import User


class NumberBoxWidthTests(TestCase):
    """Row number boxes are three characters wide; `.tons`, `.mth` and `.hours` are five, and the picker's basis one
    narrower.

    Pins both the class on every row (templates included) and the CSS rules.
    """

    def test_wide_inputs_carry_their_class(self):
        user = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        self.client.force_login(user)
        content = self.client.get(reverse('transform_create')).content.decode()
        wide = {
            'consumed-0-quantity': 'tons',
            'produced-0-quantity': 'tons',
            'machines-0-tons': 'tons',
            'machines-__prefix__-tons': 'tons',
            'machines-0-hours': 'mth',
            'machines-__prefix__-hours': 'mth',
            'workers-0-hours': 'hours',
            'workers-__prefix__-hours': 'hours',
        }
        for name, css_class in wide.items():
            with self.subTest(name=name):
                self.assertRegex(content, rf'<input(?=[^>]*name="{name}")[^>]*class="{css_class}"')
        # The other number boxes stay three characters wide.
        for name in ('machines-0-litres', 'machines-__prefix__-litres'):
            with self.subTest(name=name):
                self.assertNotRegex(content, rf'<input(?=[^>]*name="{name}")[^>]*class=')

    def test_the_stylesheet_widens_the_tons_mth_and_hours_boxes(self):
        css = (settings.BASE_DIR / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        self.assertRegex(
            css, r'\.item-row input\[type="number"\]:is\(\.tons, \.mth, \.hours\)\s*\{[^}]*width:\s*calc\(5ch'
        )
        self.assertRegex(css, r'\.item-row > div\s*\{[^}]*flex:\s*1 1 calc\(7rem - 1ch\)')


class ThemeTokenTests(TestCase):
    """Every colour token is declared in both the light and dark blocks of app.css, every `var()` names a declared
    token, and no colour literal appears below them.

    These failures are silent in the browser and invisible to view tests.
    """

    # The brand green is the same colour in both themes on purpose, and the
    # focus ring is that green at low alpha, so these five are declared once.
    SHARED = {
        '--brand-dark',
        '--brand-green',
        '--brand-green-dark',
        '--brand-green-text',
        '--focus-ring',
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        css = (settings.BASE_DIR / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        dark_at = css.index('@media (prefers-color-scheme: dark)')
        cls.css = css
        cls.light = set(re.findall(r'^\s*(--[\w-]+):', css[:dark_at], re.MULTILINE))
        cls.dark = set(
            re.findall(r'^\s*(--[\w-]+):', css[dark_at : css.index('* { box-sizing', dark_at)], re.MULTILINE)
        )
        cls.used = set(re.findall(r'var\((--[\w-]+)\)', css))

    def test_every_light_token_has_a_dark_counterpart(self):
        self.assertEqual(self.light - self.dark, self.SHARED)

    def test_the_dark_block_introduces_no_token_of_its_own(self):
        self.assertEqual(self.dark - self.light, set())

    def test_every_referenced_token_is_declared(self):
        self.assertEqual(self.used - self.light - self.dark, set())

    def test_no_rule_hardcodes_a_colour_outside_the_token_blocks(self):
        rules = self.css[self.css.index('* { box-sizing') :]
        literals = re.findall(r'#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)', rules)
        self.assertEqual(literals, [])
