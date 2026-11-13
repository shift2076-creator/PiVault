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
                'piVaultLogoLoaded', 'piVaultStatusLoaded')
ENDPOINTS = ('/users/session', '/notifications/api', '/notifications/seen', '/users/offline-mode',
             '/certificate', '/services/status')


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

    def test_settings_links_the_certificate_page_and_no_system_page(self):
        self.assertIn("'PiVault Certificate'", JS)
        self.assertNotIn("'/system'", JS)
        self.assertNotIn("'System'", JS)

    def test_offline_control_sits_left_of_the_bell_and_posts_with_csrf(self):
        self.assertIn('root.append(style,offlineButton,button,panel)', JS)
        self.assertIn("'OFFLINE'", JS)
        self.assertIn("headers:{'X-CSRFToken':state.csrf_token}", JS)
        self.assertIn('.pv-offline-on', JS)

    def test_admin_bell_keeps_its_guard(self):
        self.assertIn('window.piVaultNotificationsLoaded = true;', JS)

    def test_endpoints_are_present(self):
        for path in ENDPOINTS:
            self.assertIn(path, JS, path)

    def test_styles_are_scoped_to_the_launcher(self):
        self.assertIn('body:has(#pivault-books)', CSS)
        for selector in ('#pv-toolbar', '#pv-settings'):
            self.assertIn(selector, CSS, selector)

    def test_home_page_is_one_flat_list_without_headings_or_rules(self):
        self.assertIn('.services-group > button {\n  display: none !important;', CSS)
        self.assertNotIn('#pivault-documents) { grid-area: knowledge', CSS)

    def test_categories_are_rows_and_entries_sit_in_columns(self):
        self.assertIn('"entertainment"', CSS)
        self.assertIn('repeat(4, minmax(0, 1fr))', CSS)
        self.assertIn('repeat(2, minmax(0, 1fr))', CSS)
        self.assertIn('calc(var(--pv-column-gap) / 3)', CSS)
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

    def test_content_is_capped_at_1200px(self):
        self.assertIn('max-width: 1200px !important;', CSS)
        self.assertIn('calc((100vw - 1200px) / 2)', CSS)

    def test_favicon_files_have_the_right_sizes(self):
        import struct
        favicon = ROOT / 'stack/homepage/favicon'
        for name, size in (('favicon-16.png', 16), ('favicon-32.png', 32), ('apple-touch-icon.png', 180)):
            data = (favicon / name).read_bytes()
            self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n', name)
            self.assertEqual(struct.unpack('>II', data[16:24]), (size, size), name)
        ico = (favicon / 'favicon.ico').read_bytes()
        self.assertEqual(struct.unpack('<HHH', ico[:6]), (0, 1, 3))
        sizes = [(ico[6 + 16 * i], ico[7 + 16 * i]) for i in range(3)]
        self.assertEqual(sizes, [(16, 16), (32, 32), (48, 48)])

    def test_apple_icon_is_opaque_so_iOS_does_not_show_black_corners(self):
        data = (ROOT / 'stack/homepage/favicon/apple-touch-icon.png').read_bytes()
        self.assertEqual(data[25], 2, 'PNG colour type must be RGB (2), not RGBA')

    def test_launcher_links_the_web_app_manifest_served_by_the_portal(self):
        self.assertIn("make('link', {rel: 'manifest', href: '/manifest.webmanifest'})", JS)

    def test_favicon_block_matches_its_files(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('favicon_block', ROOT / 'tools/favicon_block.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.splice(JS), JS)

    def test_group_with_only_the_hidden_password_manager_tile_is_hidden(self):
        # Hide a group only when it holds nothing but the Password Manager tile.
        # Hiding any group that merely contains that tile also hid the Apps group.
        self.assertIn(
            '#layout-groups > .services-group:not(:has(li.service:not([data-name="Password Manager"])))',
            CSS)
        self.assertNotIn('services-group:has(li.service[data-name="Password Manager"])', CSS)

    def test_javascript_parses(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        result = subprocess.run([node, '--check', str(ROOT / 'stack/homepage-addon.js')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
