"""Public browser icons and the web app manifest, served without a session."""
import json
from pathlib import Path

from django.test import TestCase

REPO = Path(__file__).resolve().parents[3]
FAVICON = REPO / 'stack/homepage/favicon'


class PublicIconTests(TestCase):
    def test_root_favicon_is_served_without_sign_in(self):
        response = self.client.get('/favicon.ico', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/x-icon')
        self.assertEqual(response.content, (FAVICON / 'favicon.ico').read_bytes())

    def test_apple_icon_is_served_without_sign_in(self):
        response = self.client.get('/apple-touch-icon.png', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response.content, (FAVICON / 'apple-touch-icon.png').read_bytes())

    def test_manifest_is_served_and_points_at_the_apple_icon(self):
        response = self.client.get('/manifest.webmanifest', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        manifest = json.loads(response.content)
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['icons'][0]['src'], '/apple-touch-icon.png')
