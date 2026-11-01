import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
from unittest.mock import patch

from django.core import signing
from django.test import TestCase, override_settings

from portal import device_views
from portal.client_packages import release_path
from portal.models import ClientPackage, ClientRelease, Device, DeviceApp, User

HELPER_PATH = Path(device_views.HELPER)
ANDROID_ID = 'a1b2c3d4e5f60718'


def load_helper():
    spec = importlib.util.spec_from_file_location('pivault_android_sync', HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Caddy terminates TLS in production, so the portal's own HTTPS redirect is not under test here.
@override_settings(SECURE_SSL_REDIRECT=False)
class DeviceTestCase(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        override = override_settings(PIVAULT_CLIENT_ROOT=tmp.name)
        override.enable()
        self.addCleanup(override.disable)
        self.admin = User.objects.create_user(username='admin', email='admin@example.com',
            display_name='Admin', role='admin', password='Fixture-admin-42!')
        self.user = User.objects.create_user(username='user', email='user@example.com',
            display_name='User', role='user', password='Fixture-user-42!')

    def bearer(self, user):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + device_views._issue_token(user)}

    def approve(self, client='davx5', version='1.0.0', body=b'approved-apk'):
        digest = hashlib.sha256(body).hexdigest()
        release = ClientRelease.objects.create(id='release-' + client, client=client, version=version,
            sha256=digest, size=len(body), url='https://f-droid.org/repo/example.apk',
            release_url='https://f-droid.org/packages/example/')
        release_path(release).write_bytes(body)
        ClientPackage.objects.create(client=client, current=release, latest=release)
        return release

    def identify(self, android_id=ANDROID_ID, user=None):
        return self.client.post('/devices/api/identify', data=json.dumps({
            'android_id': android_id, 'model': 'Test Phone', 'android_release': '14'}),
            content_type='application/json', **self.bearer(user or self.admin))


class SyncApiTests(DeviceTestCase):
    def test_identify_stores_a_keyed_identity_not_the_android_id(self):
        response = self.identify()
        self.assertEqual(response.status_code, 200)
        device = Device.objects.get()
        self.assertEqual(device.identity, device_views._identity(ANDROID_ID))
        self.assertNotIn(ANDROID_ID, device.identity)
        self.assertEqual(device.apps.count(), len(device_views.DEVICE_PACKAGES))
        body = response.json()
        self.assertEqual(body['id'], str(device.id))
        self.assertEqual(sorted(body['apps']), sorted(device_views.DEVICE_PACKAGES))
        self.assertEqual(body['apps']['davx5']['package'], 'at.bitfire.davdroid')

    def test_identify_keeps_the_same_device_and_its_name(self):
        first = self.identify().json()['id']
        Device.objects.filter(pk=first).update(name='Greenie phone')
        second = self.identify().json()['id']
        self.assertEqual(first, second)
        self.assertEqual(Device.objects.get().name, 'Greenie phone')

    def test_identify_rejects_a_malformed_android_id(self):
        self.assertEqual(self.identify(android_id='not-hex').status_code, 400)
        self.assertEqual(Device.objects.count(), 0)

    def test_api_requires_an_admin_token(self):
        url = '/devices/api/manifest'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, **self.bearer(self.user)).status_code, 401)
        self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION='Bearer nonsense').status_code, 401)
        token = device_views._issue_token(self.admin)
        with patch('portal.device_views.TOKEN_SECONDS', -1):
            self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION='Bearer ' + token).status_code, 401)

    def test_report_updates_observed_state_and_rejects_unknown_clients(self):
        device_id = self.identify().json()['id']
        ok = self.client.post('/devices/api/%s/report' % device_id, data=json.dumps({'apps': {
            'davx5': {'state': 'enabled', 'version': '1.0.0'}}}), content_type='application/json', **self.bearer(self.admin))
        self.assertEqual(ok.status_code, 200)
        app = DeviceApp.objects.get(device_id=device_id, client='davx5')
        self.assertEqual((app.observed_state, app.observed_version), ('enabled', '1.0.0'))
        bad = self.client.post('/devices/api/%s/report' % device_id, data=json.dumps({'apps': {
            'firefox': {'state': 'enabled'}}}), content_type='application/json', **self.bearer(self.admin))
        self.assertEqual(bad.status_code, 400)

    def test_manifest_lists_only_approved_current_releases(self):
        self.approve('davx5', '1.0.0')
        apps = self.client.get('/devices/api/manifest', **self.bearer(self.admin)).json()['apps']
        self.assertEqual(set(apps), {'davx5'})
        self.assertEqual(apps['davx5']['sha256'], hashlib.sha256(b'approved-apk').hexdigest())
        self.assertNotIn('vibrdrome', device_views.DEVICE_PACKAGES)
        self.assertNotIn('firefox', device_views.DEVICE_PACKAGES)

    def test_apk_serves_only_the_approved_copy_to_a_token_holder(self):
        self.approve('davx5')
        response = self.client.get('/devices/api/apk/davx5', **self.bearer(self.admin))
        self.assertEqual(b''.join(response.streaming_content), b'approved-apk')
        self.assertEqual(self.client.get('/devices/api/apk/firefox', **self.bearer(self.admin)).status_code, 404)
        self.assertEqual(self.client.get('/devices/api/apk/vibrdrome', **self.bearer(self.admin)).status_code, 404)
        self.assertEqual(self.client.get('/devices/api/apk/davx5').status_code, 401)


class AdminPageTests(DeviceTestCase):
    def test_overview_requires_an_admin_session(self):
        self.assertEqual(self.client.get('/devices').status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/devices').status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get('/devices')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Android devices')

    def test_overview_lists_each_app_with_its_description_and_sync_column(self):
        self.identify()
        self.client.force_login(self.admin)
        response = self.client.get('/devices')
        self.assertContains(response, 'Password manager for your Vaultwarden server')
        self.assertContains(response, 'Sync to phone')
        self.assertContains(response, 'Organic Maps')

    def test_rename_and_set_app_state(self):
        device_id = self.identify().json()['id']
        self.client.force_login(self.admin)
        self.client.post('/devices/%s/rename' % device_id, {'name': 'Work phone'})
        self.assertEqual(Device.objects.get().name, 'Work phone')
        self.assertEqual(self.client.post('/devices/%s/rename' % device_id, {'name': ''}).status_code, 400)
        self.client.post('/devices/%s/apps/davx5' % device_id, {'enabled': 'off'})
        self.assertFalse(DeviceApp.objects.get(device_id=device_id, client='davx5').enabled)
        self.assertEqual(self.client.post('/devices/%s/apps/firefox' % device_id, {'enabled': 'on'}).status_code, 404)
        self.assertEqual(self.client.post('/devices/%s/apps/vibrdrome' % device_id, {'enabled': 'on'}).status_code, 404)

    def test_helper_download_embeds_a_working_token_and_the_origin(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/devices/sync.py').status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get('/devices/sync.py')
        self.assertEqual(response.status_code, 200)
        script = response.content.decode()
        self.assertNotIn('__PIVAULT_TOKEN__', script)
        self.assertNotIn('__PIVAULT_BASE__', script)
        self.assertIn("BASE = 'https://192.168.4.90:8443'", script)
        token = re.search(r"TOKEN = '([^']+)'", script).group(1)
        self.assertEqual(signing.loads(token, salt=device_views.TOKEN_SALT, max_age=device_views.TOKEN_SECONDS)['uid'], self.admin.pk)


class HelperParsingTests(TestCase):
    def setUp(self):
        self.helper = load_helper()

    def test_parse_devices_separates_ready_and_unauthorized(self):
        output = 'List of devices attached\nABC123\tdevice\nXYZ999\tunauthorized\n\n'
        self.assertEqual(self.helper.parse_devices(output), (['ABC123'], ['XYZ999']))

    def test_parse_android_id_rejects_null(self):
        self.assertEqual(self.helper.parse_android_id(' a1b2c3d4e5f60718\n'), 'a1b2c3d4e5f60718')
        self.assertIsNone(self.helper.parse_android_id('null\n'))

    def test_parse_version_name_and_pm_state(self):
        self.assertEqual(self.helper.parse_version_name('    versionName=2.3.1\n'), '2.3.1')
        self.assertIsNone(self.helper.parse_version_name('no version here'))
        self.assertEqual(self.helper.parse_pm_state('Package org.adaway new state: disabled-user'), 'disabled-user')

    def test_source_keeps_its_placeholders(self):
        source = HELPER_PATH.read_text(encoding='utf-8')
        self.assertIn("BASE = '__PIVAULT_BASE__'", source)
        self.assertIn("TOKEN = '__PIVAULT_TOKEN__'", source)


class CaCertificateTests(DeviceTestCase):
    def test_root_certificate_is_admin_only_and_served_when_installed(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/devices/ca.crt').status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/devices/ca.crt').status_code, 404)
        with override_settings(DATA_DIR=self.root):
            (self.root / 'caddy-root.crt').write_bytes(b'-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n')
            response = self.client.get('/devices/ca.crt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n')


class SystemPageTests(DeviceTestCase):
    def test_system_page_no_longer_links_to_devices(self):
        # The device sync entry lives in the Settings menu (stack/homepage-addon.js), not on the System page.
        self.client.force_login(self.admin)
        response = self.client.get('/system')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="android-devices"')
        self.assertNotContains(response, 'Manage phones and approved app sync')
