"""Static checks for the PiVault launcher assets: stack/homepage/custom.css and stack/homepage-addon.js.

These run without a browser. Behaviour in a live Homepage must still be checked on the Pi.
"""
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = (ROOT / 'stack/homepage/custom.css').read_text()
JS = (ROOT / 'stack/homepage-addon.js').read_text()
# Allowed: the SVG namespace (never fetched) and the Radicale link, which uses the launcher's own host.
EXTERNAL_URL = re.compile(r'https?://(?!www\.w3\.org/2000/svg)(?!\$\{location\.hostname\})')
MODULE_FLAGS = ('piVaultTitleLoaded', 'piVaultUsersLoaded', 'piVaultNotificationsLoaded',
                'piVaultSearchLoaded', 'piVaultLogoLoaded', 'piVaultStatusLoaded')
ENDPOINTS = ('/users/session', '/notifications/api', '/notifications/seen',
             '/search/apps', '/search', '/services/status')


class LauncherAssetTests(unittest.TestCase):
    def test_no_external_resources(self):
        for name, text in (('custom.css', CSS), ('homepage-addon.js', JS)):
            self.assertIsNone(EXTERNAL_URL.search(text), name)
        self.assertNotIn('@import', CSS)

    def test_title_is_a_single_heading_not_a_pseudo_element(self):
        self.assertNotIn('content: "PiVault"', CSS)
        self.assertIn('#pv-title', CSS)
        self.assertIn("make('h1', {id: 'pv-title'", JS)

    def test_status_dot_states_are_styled(self):
        for state in ('running', 'attention', 'stopped', 'unknown'):
            self.assertIn(f'.pv-dot[data-state="{state}"]', CSS)
            self.assertIn(state, JS)

    def test_each_module_loads_once(self):
        for flag in MODULE_FLAGS:
            self.assertEqual(JS.count(f'window.{flag} = true'), 1, flag)

    def test_admin_bell_keeps_its_guard(self):
        self.assertIn('window.piVaultNotificationsLoaded = true;', JS)

    def test_endpoints_are_present(self):
        for path in ENDPOINTS:
            self.assertIn(path, JS, path)

    def test_styles_are_scoped_to_the_launcher(self):
        self.assertIn('body:has(#pivault-books)', CSS)
        for selector in ('#pv-toolbar', '#pv-settings', '#pv-search'):
            self.assertIn(selector, CSS, selector)

    def test_categories_are_rows_and_entries_sit_in_columns(self):
        self.assertIn('"entertainment"', CSS)
        self.assertNotIn('repeat(4, minmax(0, 1fr))', CSS)
        self.assertIn('flex-wrap: wrap', CSS)
        self.assertIn('div:has(.services-list)', CSS)

    def test_title_is_centred_with_an_icon_at_text_height(self):
        self.assertIn('justify-content: center', CSS)
        self.assertIn('#pv-title::before', CSS)
        self.assertIn('height: 1.1em;', CSS)

    def test_tile_panel_is_always_shown_and_not_clipped(self):
        self.assertNotIn('clip-path', CSS)
        self.assertNotIn('opacity: 0;\n  pointer-events', CSS)

    def test_hover_drops_the_shadow_and_darkens_the_top_edge(self):
        self.assertIn('box-shadow: 0 0 0 0 rgb(0 0 0 / 0%), inset 0 1px 0 #10161d;', CSS)

    def test_javascript_parses(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        result = subprocess.run([node, '--check', str(ROOT / 'stack/homepage-addon.js')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
