import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import zipfile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from portal.client_packages import (FIREFOX_ID, apply_release, client_rows, maintain,
                             record_release, release_path, safe_url)
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

    def approve(self, release):
        self.client.force_login(self.admin)
        return self.client.post('/notifications/' + release.notification_id + '/resolve',
            {'action': 'approve'}, secure=True, HTTP_ACCEPT='application/json')

    def test_discovery_only_notifies_and_never_downloads(self):
        release, _ = self.release()
        self.assertFalse(release_path(release).exists())
        self.assertIsNone(ClientPackage.objects.get(client='firefox').current)
        self.assertEqual(release.notification.kind, 'clients.update')
        self.assertEqual(release.notification.resolution, '')

    def test_discovery_is_idempotent_and_does_not_reopen_skipped_version(self):
        release, _ = self.release()
        self.client.force_login(self.admin)
        self.client.post('/notifications/' + release.notification_id + '/resolve', {'action': 'skip'}, secure=True)
        self.release()
        self.assertEqual(Notification.objects.filter(kind='clients.update').count(), 1)
        release.notification.refresh_from_db()
        self.assertEqual(release.notification.status, 'resolved')
        self.assertEqual(client_rows()[1]['status'], 'Skipped this version')

    def test_regular_users_cannot_approve_or_download(self):
        release, _ = self.release()
        self.client.force_login(self.user)
        self.assertEqual(self.client.post('/notifications/' + release.notification_id + '/resolve',
            {'action': 'approve'}, secure=True).status_code, 403)
        self.assertEqual(self.client.get('/users/clients/firefox', secure=True).status_code, 403)
        release.notification.refresh_from_db()
        self.assertEqual(release.notification.resolution, '')

    def test_approval_is_csrf_protected(self):
        release, _ = self.release()
        client = Client(enforce_csrf_checks=True); client.force_login(self.admin)
        self.assertEqual(client.post('/notifications/' + release.notification_id + '/resolve',
            {'action': 'approve'}, secure=True).status_code, 403)

    def test_approval_queues_without_fetching_or_publishing(self):
        release, _ = self.release()
        with patch('portal.client_packages.http_open') as request:
            response = self.approve(release)
        self.assertEqual(response.status_code, 202)
        request.assert_not_called()
        self.assertIsNone(ClientPackage.objects.get(client='firefox').current_id)
        self.assertFalse(release_path(release).exists())

    def test_unapproved_release_is_never_downloaded(self):
        release, _ = self.release()
        with patch('portal.client_packages.http_open') as request:
            self.assertFalse(apply_release(release))
        request.assert_not_called()

    def test_approved_exact_copy_is_published_and_notification_resolves(self):
        release, data = self.release(); self.approve(release); release.refresh_from_db()
        with patch('portal.client_packages.http_open', return_value=io.BytesIO(data)):
            self.assertTrue(apply_release(release))
        self.assertEqual(release_path(release).read_bytes(), data)
        self.assertEqual(ClientPackage.objects.get(client='firefox').current_id, release.id)
        release.notification.refresh_from_db()
        self.assertEqual(release.notification.status, 'resolved')
        self.user.vault_user_id = 'own-vault'; self.user.service_access = {'passwords': 'isolated'}
        self.user.save(); self.client.force_login(self.user)
        response = self.client.get('/users/clients/firefox', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/x-xpinstall')
        response.close()

    def test_bad_digest_keeps_previous_copy_and_does_not_resolve_approval(self):
        old, data = self.release('2026.9.0'); self.approve(old); old.refresh_from_db()
        with patch('portal.client_packages.http_open', return_value=io.BytesIO(data)):
            apply_release(old)
        new, incoming = self.release('2026.10.0'); self.approve(new); new.refresh_from_db()
        wrong = incoming[:-1] + bytes([incoming[-1] ^ 1])
        with patch('portal.client_packages.http_open', return_value=io.BytesIO(wrong)):
            with self.assertRaisesMessage(Exception, 'checksum'):
                apply_release(new)
        self.assertEqual(ClientPackage.objects.get(client='firefox').current_id, old.id)
        self.assertEqual(release_path(old).read_bytes(), data)
        new.notification.refresh_from_db(); self.assertEqual(new.notification.status, 'open')
        self.assertFalse(release_path(new).exists())

    def test_old_failed_approval_cannot_downgrade_a_newer_published_copy(self):
        old, _ = self.release('2026.9.0'); self.approve(old); old.refresh_from_db()
        new, data = self.release(); self.approve(new); new.refresh_from_db()
        with patch('portal.client_packages.http_open', return_value=io.BytesIO(data)):
            apply_release(new)
        with patch('portal.client_packages.http_open') as request:
            self.assertFalse(apply_release(old))
        request.assert_not_called()
        self.assertEqual(ClientPackage.objects.get(client='firefox').current_id, new.id)

    def test_check_only_does_not_apply_even_an_approved_release(self):
        release, _ = self.release(); self.approve(release)
        with patch('portal.client_packages.apply_release') as apply, patch('portal.client_packages.discover', side_effect=OSError):
            maintain(check_only=True)
        apply.assert_not_called()
        self.assertIsNone(ClientPackage.objects.get(client='firefox').current_id)

    def test_metadata_failure_keeps_cached_copy_and_reports_to_shared_inbox(self):
        release, data = self.release(); self.approve(release); release.refresh_from_db()
        with patch('portal.client_packages.http_open', return_value=io.BytesIO(data)):
            apply_release(release)
        with patch('portal.client_packages.discover', side_effect=OSError):
            self.assertEqual(maintain(), ['android', 'firefox'])
        self.assertEqual(ClientPackage.objects.get(client='firefox').current_id, release.id)
        self.assertTrue(Notification.objects.filter(source='clients', level='warning').exists())

    def test_downstream_urls_cannot_redirect_to_private_or_arbitrary_hosts(self):
        for url in ('http://addons.mozilla.org/a.xpi', 'https://127.0.0.1/a',
                    'https://addons.mozilla.org.evil.example/a', 'https://user:secret@github.com/a'):
            with self.assertRaises(Exception): safe_url(url)

    def test_admin_page_shows_local_version_and_pending_release(self):
        self.release(); self.client.force_login(self.admin)
        response = self.client.get('/users/add', secure=True)
        self.assertContains(response, 'Local apps and extensions')
        self.assertContains(response, '2026.10.0')
        self.assertContains(response, 'Approval needed')
        self.assertContains(response, 'Review update')
