import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import zipfile
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from portal.client_packages import (CLIENTS, FIREFOX_ID, apply_release, discover, maintain,
                             record_release, release_path, safe_url, validate_release, verify_package)
from portal.models import ClientPackage, ClientRelease, Notification, User


class ClientPackageTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = override_settings(PIVAULT_CLIENT_ROOT=self.tmp.name)
        self.settings.enable(); self.addCleanup(self.settings.disable)
        self.admin = User.objects.create_user(username='admin', email='admin@example.com',
            display_name='Admin', role='admin', password='Fixture-admin-42!')
        self.user = User.objects.create_user(username='user', email='user@example.com',
            display_name='User', role='user', password='Fixture-user-42!')

    def release(self, version='2026.10.0'):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'version': version,
                'browser_specific_settings': {'gecko': {'id': FIREFOX_ID}}}))
            archive.writestr('META-INF/mozilla.rsa', b'fixture-signing-placeholder')
        data = stream.getvalue()
        metadata = {'version': version, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
            'url': 'https://addons.mozilla.org/firefox/downloads/file/1/bitwarden.xpi',
            'release_url': 'https://addons.mozilla.org/en-US/firefox/addon/bitwarden-password-manager/'}
        record_release('firefox', metadata)
        return ClientRelease.objects.get(sha256=metadata['sha256']), data

    def nightly(self, data):
        # One nightly run: the HTTP fixture serves the stored release; discovery is skipped.
        with patch('portal.client_packages.discover', side_effect=OSError), \
                patch('portal.client_packages.http_open', return_value=io.BytesIO(data)):
            return maintain()

    def test_discovered_release_is_downloaded_and_published_without_approval(self):
        release, data = self.release()
        self.nightly(data)
        self.assertEqual(release_path(release).read_bytes(), data)
        self.assertEqual(ClientPackage.objects.get(client='firefox').current_id, release.id)
        self.assertFalse(Notification.objects.filter(kind='clients.update', status='open').exists())

    def test_discovery_only_records_and_never_downloads(self):
        release, _ = self.release()
        with patch('portal.client_packages.discover', side_effect=OSError), \
                patch('portal.client_packages.http_open') as request:
            maintain(check_only=True)
        request.assert_not_called()
        self.assertFalse(release_path(release).exists())
        self.assertIsNone(ClientPackage.objects.get(client='firefox').current_id)

    def test_older_release_never_replaces_a_newer_copy(self):
        old, old_data = self.release('2026.9.0')
        self.nightly(old_data)
        new, new_data = self.release('2026.10.0')
        self.nightly(new_data)
        with patch('portal.client_packages.http_open') as request:
            self.assertFalse(apply_release(old))
        request.assert_not_called()
        self.assertEqual(ClientPackage.objects.get(client='firefox').current_id, new.id)

    def test_failed_verification_keeps_previous_copy_and_retries_next_run(self):
        old, old_data = self.release('2026.9.0')
        self.nightly(old_data)
        new, new_data = self.release('2026.10.0')
        wrong = new_data[:-1] + bytes([new_data[-1] ^ 1])
        with patch('portal.client_packages.discover', side_effect=OSError), \
                patch('portal.client_packages.http_open', return_value=io.BytesIO(wrong)):
            self.assertIn('firefox', maintain())
        self.assertEqual(ClientPackage.objects.get(client='firefox').current_id, old.id)
        self.assertEqual(release_path(old).read_bytes(), old_data)
        self.assertFalse(release_path(new).exists())
        self.assertTrue(Notification.objects.filter(source='clients', level='error').exists())
        self.nightly(new_data)
        self.assertEqual(ClientPackage.objects.get(client='firefox').current_id, new.id)

    def test_published_copy_is_downloadable_by_users_with_vault_access(self):
        release, data = self.release()
        self.nightly(data)
        self.user.vault_user_id = 'own-vault'; self.user.service_access = {'passwords': 'isolated'}
        self.user.save(); self.client.force_login(self.user)
        response = self.client.get('/users/clients/firefox', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/x-xpinstall')
        response.close()

    def test_downloads_are_not_open_to_users_without_vault_access(self):
        self.nightly(self.release()[1])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/users/clients/firefox', secure=True).status_code, 403)

    def test_metadata_failure_keeps_cached_copy_and_reports_to_shared_inbox(self):
        release, data = self.release()
        self.nightly(data)
        with patch('portal.client_packages.discover', side_effect=OSError):
            self.assertEqual(maintain(), sorted(CLIENTS))
        self.assertEqual(ClientPackage.objects.get(client='firefox').current_id, release.id)
        self.assertTrue(Notification.objects.filter(source='clients', level='warning').exists())

    def test_downstream_urls_cannot_redirect_to_private_or_arbitrary_hosts(self):
        for url in ('http://addons.mozilla.org/a.xpi', 'https://127.0.0.1/a',
                    'https://addons.mozilla.org.evil.example/a', 'https://user:secret@github.com/a'):
            with self.assertRaises(Exception): safe_url(url)

    def test_admin_page_shows_local_version_and_pending_download(self):
        self.release(); self.client.force_login(self.admin)
        response = self.client.get('/users/add', secure=True)
        self.assertContains(response, 'Local apps and extensions')
        self.assertContains(response, '2026.10.0')
        self.assertContains(response, 'Download pending')
        self.assertNotContains(response, 'Approval needed')
        self.assertNotContains(response, 'Review update')

BITWARDEN_URL = 'https://github.com/bitwarden/android/releases/download/v2026.10.0-bwpm/com.x8bit.bitwarden.apk'
BITWARDEN_RELEASE_URL = 'https://github.com/bitwarden/android/releases/tag/v2026.10.0-bwpm'


def android_metadata(**overrides):
    data = {'version': '2026.10.0', 'sha256': 'a' * 64, 'size': 1024,
        'url': BITWARDEN_URL, 'release_url': BITWARDEN_RELEASE_URL}
    data.update(overrides)
    return data


class ClientRegistryTests(SimpleTestCase):
    """Pins the Bitwarden (android) rules that the registry must preserve."""

    def test_android_discovery_selects_the_stable_bitwarden_release(self):
        releases = [
            {'draft': False, 'prerelease': True, 'tag_name': 'v2026.11.0-bwpm', 'html_url': BITWARDEN_RELEASE_URL,
             'assets': []},
            {'draft': False, 'prerelease': False, 'tag_name': 'v2026.10.0-bwpm', 'html_url': BITWARDEN_RELEASE_URL,
             'assets': [{'name': 'com.x8bit.bitwarden.apk', 'size': 1024, 'digest': 'sha256:' + 'b' * 64,
                         'browser_download_url': BITWARDEN_URL}]},
        ]
        with patch('portal.client_packages.read_json', return_value=releases):
            data = discover('android')
        self.assertEqual(data, {'version': '2026.10.0', 'sha256': 'b' * 64, 'size': 1024,
            'url': BITWARDEN_URL, 'release_url': BITWARDEN_RELEASE_URL})

    def test_android_discovery_refuses_a_release_without_an_upstream_digest(self):
        releases = [{'draft': False, 'prerelease': False, 'tag_name': 'v2026.10.0-bwpm',
            'html_url': BITWARDEN_RELEASE_URL,
            'assets': [{'name': 'com.x8bit.bitwarden.apk', 'size': 1024, 'digest': '',
                        'browser_download_url': BITWARDEN_URL}]}]
        with patch('portal.client_packages.read_json', return_value=releases):
            with self.assertRaisesMessage(ValidationError, 'no upstream SHA-256 digest'):
                discover('android')

    def test_android_accepts_only_the_bitwarden_apk_path(self):
        self.assertEqual(validate_release('android', android_metadata())['version'], '2026.10.0')
        for url in ('https://github.com/evil/app/releases/download/v1/com.x8bit.bitwarden.apk',
                    'https://github.com/bitwarden/android/releases/download/v1/other.apk',
                    'https://github.com/bitwarden/android/releases/download/v1/com.x8bit.bitwarden.apk.zip'):
            with self.subTest(url=url):
                with self.assertRaisesMessage(ValidationError, 'Unexpected Android release artifact'):
                    validate_release('android', android_metadata(url=url))

    def test_firefox_accepts_only_mozilla_xpi_artifacts(self):
        firefox = {'version': '2026.10.0', 'sha256': 'c' * 64, 'size': 1024,
            'url': 'https://addons.mozilla.org/firefox/downloads/file/1/bitwarden.xpi',
            'release_url': 'https://addons.mozilla.org/en-US/firefox/addon/bitwarden-password-manager/'}
        validate_release('firefox', firefox)
        with self.assertRaisesMessage(ValidationError, 'Unexpected Firefox release artifact'):
            validate_release('firefox', dict(firefox, url='https://github.com/bitwarden/android/releases/download/v1/x.xpi'))

    def test_unknown_clients_and_extra_metadata_fields_are_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'Invalid client release metadata'):
            validate_release('ios', android_metadata())
        with self.assertRaisesMessage(ValidationError, 'Invalid client release metadata'):
            validate_release('android', android_metadata(extra='field'))

    def test_android_package_must_contain_an_apk_manifest(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('classes.dex', b'not-a-manifest')
        data = stream.getvalue()
        release = SimpleNamespace(client='android', size=len(data), version='2026.10.0',
            sha256=hashlib.sha256(data).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'package.apk'
            path.write_bytes(data)
            with self.assertRaisesMessage(ValidationError, 'not an APK'):
                verify_package(path, release)


class ClientRegistryConsistencyTests(SimpleTestCase):
    def test_every_client_has_one_rule_discoverer_and_archive_check(self):
        from portal import client_packages
        keys = set(client_packages.CLIENTS)
        self.assertEqual(set(client_packages.ARTIFACT_RULES), keys)
        self.assertEqual(set(client_packages.DISCOVERERS), keys)
        self.assertEqual(set(client_packages.ARCHIVE_CHECKS), keys)

    def test_cache_blob_names_match_only_known_clients_and_extensions(self):
        from portal.client_packages import _is_cache_blob
        digest = 'a' * 64
        self.assertTrue(_is_cache_blob('android-' + digest + '.apk'))
        self.assertTrue(_is_cache_blob('firefox-' + digest + '.xpi'))
        for name in ('android-' + digest + '.xpi', 'ios-' + digest + '.ipa', 'android-' + digest[:-1] + 'G.apk',
                     'android-' + digest + '.apk.tmp', '.client-abc123', 'android-' + digest + '.apk/'):
            with self.subTest(name=name):
                self.assertFalse(_is_cache_blob(name))


def fdroid_fixture(package_id='org.adaway'):
    def version(code, name, digest):
        return {'file': {'name': '/%s_%d.apk' % (package_id, code), 'sha256': digest, 'size': 1000 + code},
            'manifest': {'versionCode': code, 'versionName': name}}
    return {'packages': {package_id: {'versions': {
        'h1': version(60103, '6.1.3', 'a' * 64),
        'h2': version(60104, '6.1.4', 'b' * 64),
        'h3': version(60200, '6.2.0-beta1', 'c' * 64),
    }}}}


class FDroidSourceTests(SimpleTestCase):
    def setUp(self):
        from portal import client_packages
        self.cp = client_packages
        self.cp._INDEX_CACHE.clear()
        self.addCleanup(self.cp._INDEX_CACHE.clear)

    def test_discovery_picks_the_highest_stable_version_code(self):
        with patch('portal.client_packages.read_json', return_value=fdroid_fixture()):
            data = self.cp._discover_fdroid('org.adaway')
        self.assertEqual(data, {'version': '6.1.4', 'sha256': 'b' * 64, 'size': 61104,
            'url': 'https://f-droid.org/repo/org.adaway_60104.apk',
            'release_url': 'https://f-droid.org/packages/org.adaway/'})

    def test_discovery_refuses_a_package_missing_from_the_index(self):
        with patch('portal.client_packages.read_json', return_value=fdroid_fixture()):
            with self.assertRaisesMessage(ValidationError, 'does not list org.other'):
                self.cp._discover_fdroid('org.other')

    def test_discovery_refuses_a_package_with_only_prereleases(self):
        fixture = fdroid_fixture()
        fixture['packages']['org.adaway']['versions'] = {'h3': fixture['packages']['org.adaway']['versions']['h3']}
        with patch('portal.client_packages.read_json', return_value=fixture):
            with self.assertRaisesMessage(ValidationError, 'no stable release'):
                self.cp._discover_fdroid('org.adaway')

    def test_artifact_rule_accepts_only_the_package_apk_under_f_droid_repo(self):
        self.cp._fdroid_artifact_rule('org.adaway', 'https://f-droid.org/repo/org.adaway_60104.apk')
        for url in ('https://f-droid.org/repo/org.other_1.apk', 'https://evil.example/repo/org.adaway_1.apk',
                    'https://f-droid.org/archive/org.adaway_1.apk', 'https://f-droid.org/repo/org.adaway_1.zip'):
            with self.subTest(url=url):
                with self.assertRaisesMessage(ValidationError, 'Unexpected F-Droid release artifact'):
                    self.cp._fdroid_artifact_rule('org.adaway', url)

    def test_index_is_read_once_per_maintenance_run_and_uses_the_larger_limit(self):
        with patch('portal.client_packages.read_json', return_value=fdroid_fixture()) as read:
            self.cp.fdroid_index(); self.cp.fdroid_index()
        read.assert_called_once_with('https://f-droid.org/repo/index-v2.json', limit=128 * 1024 * 1024)

    def test_bitwarden_and_firefox_keep_their_download_names(self):
        self.assertEqual(self.cp.download_filename('android', '2026.10.0'), 'Bitwarden-2026.10.0.apk')
        self.assertEqual(self.cp.download_filename('firefox', '2026.10.0'), 'Bitwarden-2026.10.0.xpi')

    def test_every_client_has_a_download_name(self):
        self.assertEqual(set(self.cp.DOWNLOAD_NAMES), set(self.cp.CLIENTS))


class FdroidClientTests(SimpleTestCase):
    def test_discovers_the_f_droid_build_and_names_the_download(self):
        from portal import client_packages as cp
        cp._INDEX_CACHE.clear()
        self.addCleanup(cp._INDEX_CACHE.clear)
        with patch('portal.client_packages.read_json', return_value=fdroid_fixture('org.fdroid.fdroid')):
            data = cp.discover('fdroid')
        self.assertEqual(data['url'], 'https://f-droid.org/repo/org.fdroid.fdroid_60104.apk')
        self.assertEqual(data['version'], '6.1.4')
        self.assertEqual(cp.download_filename('fdroid', '6.1.4'), 'F-Droid-6.1.4.apk')


class AdawayClientTests(SimpleTestCase):
    def test_discovers_the_f_droid_build_and_names_the_download(self):
        from portal import client_packages as cp
        cp._INDEX_CACHE.clear()
        self.addCleanup(cp._INDEX_CACHE.clear)
        with patch('portal.client_packages.read_json', return_value=fdroid_fixture('org.adaway')):
            data = cp.discover('adaway')
        self.assertEqual(data['url'], 'https://f-droid.org/repo/org.adaway_60104.apk')
        self.assertEqual(data['version'], '6.1.4')
        self.assertEqual(cp.download_filename('adaway', '6.1.4'), 'AdAway-6.1.4.apk')


class Davx5ClientTests(SimpleTestCase):
    def test_discovers_the_f_droid_build_and_names_the_download(self):
        from portal import client_packages as cp
        cp._INDEX_CACHE.clear()
        self.addCleanup(cp._INDEX_CACHE.clear)
        with patch('portal.client_packages.read_json', return_value=fdroid_fixture('at.bitfire.davdroid')):
            data = cp.discover('davx5')
        self.assertEqual(data['url'], 'https://f-droid.org/repo/at.bitfire.davdroid_60104.apk')
        self.assertEqual(data['version'], '6.1.4')
        self.assertEqual(cp.download_filename('davx5', '6.1.4'), 'DAVx5-6.1.4.apk')


class JellyfinClientTests(SimpleTestCase):
    def test_discovers_the_f_droid_build_and_names_the_download(self):
        from portal import client_packages as cp
        cp._INDEX_CACHE.clear()
        self.addCleanup(cp._INDEX_CACHE.clear)
        with patch('portal.client_packages.read_json', return_value=fdroid_fixture('org.jellyfin.mobile')):
            data = cp.discover('jellyfin')
        self.assertEqual(data['url'], 'https://f-droid.org/repo/org.jellyfin.mobile_60104.apk')
        self.assertEqual(data['version'], '6.1.4')
        self.assertEqual(cp.download_filename('jellyfin', '6.1.4'), 'Jellyfin-6.1.4.apk')


class OrganicmapsClientTests(SimpleTestCase):
    def test_discovers_the_f_droid_build_and_names_the_download(self):
        from portal import client_packages as cp
        cp._INDEX_CACHE.clear()
        self.addCleanup(cp._INDEX_CACHE.clear)
        with patch('portal.client_packages.read_json', return_value=fdroid_fixture('app.organicmaps')):
            data = cp.discover('organicmaps')
        self.assertEqual(data['url'], 'https://f-droid.org/repo/app.organicmaps_60104.apk')
        self.assertEqual(data['version'], '6.1.4')
        self.assertEqual(cp.download_filename('organicmaps', '6.1.4'), 'OrganicMaps-6.1.4.apk')


class GitHubSourceTests(SimpleTestCase):
    def test_github_discovery_refuses_more_than_one_matching_apk(self):
        from portal import client_packages as cp
        import re as _re
        release = {'draft': False, 'prerelease': False, 'tag_name': 'v1.0.0', 'html_url': 'https://github.com/o/r/releases/tag/v1.0.0',
            'assets': [{'name': 'app-arm64.apk', 'size': 1, 'digest': 'sha256:' + 'd' * 64, 'browser_download_url': 'https://github.com/o/r/releases/download/v1.0.0/app-arm64.apk'},
                       {'name': 'app-x86.apk', 'size': 1, 'digest': 'sha256:' + 'e' * 64, 'browser_download_url': 'https://github.com/o/r/releases/download/v1.0.0/app-x86.apk'}]}
        with patch('portal.client_packages.read_json', return_value=[release]):
            with self.assertRaisesMessage(ValidationError, 'exactly one matching APK'):
                cp._discover_github('o/r', '', _re.compile(r'.+\.apk'))


class VibrdromeClientTests(SimpleTestCase):
    def test_discovers_the_github_apk_and_names_the_download(self):
        from portal import client_packages as cp
        url = 'https://github.com/ddmoney420/vibrdrome-android/releases/download/v1.2.3/vibrdrome.apk'
        release = {'draft': False, 'prerelease': False, 'tag_name': 'v1.2.3',
            'html_url': 'https://github.com/ddmoney420/vibrdrome-android/releases/tag/v1.2.3',
            'assets': [{'name': 'vibrdrome.apk', 'size': 2048, 'digest': 'sha256:' + 'f' * 64,
                        'browser_download_url': url}]}
        with patch('portal.client_packages.read_json', return_value=[release]):
            data = cp.discover('vibrdrome')
        self.assertEqual(data['version'], '1.2.3')
        self.assertEqual(data['url'], url)
        self.assertEqual(cp.download_filename('vibrdrome', '1.2.3'), 'Vibrdrome-1.2.3.apk')
        with self.assertRaisesMessage(ValidationError, 'Unexpected Android release artifact'):
            cp.validate_release('vibrdrome', dict(data, url='https://github.com/evil/app/releases/download/v1/vibrdrome.apk'))
