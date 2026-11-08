"""Public browser icons and the web app manifest, served without a session."""
import json
import unittest
from pathlib import Path

from django.test import TestCase

# The portal serves the copies in its own static folder. The image build copies only app/, so
# stack/ exists only in a repository checkout.
STATIC = Path(__file__).resolve().parents[1] / 'static/portal'
STACK_FAVICON = Path(__file__).resolve().parents[3] / 'stack/homepage/favicon'


class PublicIconTests(TestCase):
    def test_root_favicon_is_served_without_sign_in(self):
        response = self.client.get('/favicon.ico', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/x-icon')
        self.assertEqual(response.content, (STATIC / 'favicon.ico').read_bytes())

    def test_apple_icon_is_served_without_sign_in(self):
        response = self.client.get('/apple-touch-icon.png', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response.content, (STATIC / 'apple-touch-icon.png').read_bytes())

    def test_manifest_is_served_and_points_at_the_apple_icon(self):
        response = self.client.get('/manifest.webmanifest', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        manifest = json.loads(response.content)
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['icons'][0]['src'], '/apple-touch-icon.png')


class IconCopyTests(TestCase):
    @unittest.skipUnless(STACK_FAVICON.is_dir(), 'stack/ is not part of the image build')
    def test_app_icons_match_the_repository_icons(self):
        for name in ('favicon.ico', 'apple-touch-icon.png'):
            self.assertEqual((STATIC / name).read_bytes(), (STACK_FAVICON / name).read_bytes(), name)
