import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from portal.models import Notification, NotificationReceipt, User
from portal.notifications import ingest, notify


class NotificationTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.override = override_settings(PIVAULT_NOTIFICATION_ROOT=str(self.root))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.admin = User.objects.create_user(username='a', email='a@example.com', display_name='Admin A', role='admin')
        self.other_admin = User.objects.create_user(username='b', email='b@example.com', display_name='Admin B', role='admin')
        self.user = User.objects.create_user(username='u', email='u@example.com', display_name='User', role='user')

    def movie_event(self):
        details = dict(movie_key='a-film-1920', genre='comedy', film_title='A Film', year=1920,
                       source_url='https://archive.org/download/a-film/a-film.mp4', source_md5='a' * 32,
                       actual_bytes=100000, reference_minutes=90.0, height=480, reference_mbps=1.2,
                       estimated_bytes=810000000, minimum_bytes=202500000, maximum_bytes=1417500000,
                       tolerance_percent=75)
        event_id = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
        payload = dict(version=1, id=event_id, source='cinema', kind='cinema.filesize', level='warning',
                       title='Review movie download', message='Size outside the expected range.',
                       details=details, created_at=timezone.now().isoformat())
        (self.root / 'inbox').mkdir(exist_ok=True)
        (self.root / 'inbox' / (event_id + '.json')).write_text(json.dumps(payload))
        return payload

    def resolve(self, event_id, action):
        return self.client.post('/notifications/' + event_id + '/resolve', {'action': action},
                                secure=True, HTTP_ACCEPT='application/json')

    def test_guest_and_user_cannot_read_or_resolve_even_with_forged_admin_header(self):
        value = notify('wiki', 'incident1', 'Notice', 'Needs attention')
        self.assertEqual(self.client.get('/notifications/api', secure=True).status_code, 401)
        self.client.force_login(self.user)
        for url in ['/notifications/api', '/notifications', '/notifications/' + value.id, '/notifications/bell.js']:
            self.assertEqual(self.client.get(url, secure=True, HTTP_X_PIVAULT_ROLE='admin').status_code, 403)
        self.assertEqual(self.resolve(value.id, 'resolve').status_code, 403)
        value.refresh_from_db()
        self.assertEqual(value.status, 'open')

    def test_unread_receipts_are_per_admin_and_open_event_remains_visible(self):
        value = notify('wiki', 'incident1', 'Wiki notice', 'Needs attention')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/notifications/api', secure=True).json()['unread_count'], 1)
        self.client.post('/notifications/seen', {'ids': [value.id]}, secure=True)
        self.client.post('/notifications/seen', {'ids': [value.id]}, secure=True)
        feed = self.client.get('/notifications/api', secure=True).json()
        self.assertEqual((feed['unread_count'], feed['open_count']), (0, 1))
        self.assertEqual(NotificationReceipt.objects.count(), 1)
        self.client.force_login(self.other_admin)
        self.assertEqual(self.client.get('/notifications/api', secure=True).json()['unread_count'], 1)

    def test_inbox_survives_absent_browser_and_import_is_idempotent(self):
        payload = self.movie_event()
        self.assertTrue((self.root / 'inbox' / (payload['id'] + '.json')).exists())
        self.assertEqual(Notification.objects.count(), 0)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/notifications/api', secure=True).json()['open_count'], 1)
        self.movie_event()
        self.client.get('/notifications/api', secure=True)
        self.assertEqual(Notification.objects.count(), 1)

    def test_approve_publishes_exact_source_event_and_records_admin(self):
        payload = self.movie_event()
        ingest()
        self.client.force_login(self.admin)
        response = self.resolve(payload['id'], 'approve')
        self.assertEqual(response.status_code, 200)
        decision = json.loads((self.root / 'decisions' / (payload['id'] + '.json')).read_text())
        self.assertEqual((decision['id'], decision['action'], decision['admin_id']), (payload['id'], 'approve', self.admin.pk))
        value = Notification.objects.get()
        self.assertEqual(value.status, 'resolved')
        self.assertEqual(value.resolved_by, self.admin)

    def test_second_admin_cannot_replace_an_existing_decision(self):
        payload = self.movie_event()
        ingest()
        self.client.force_login(self.admin)
        self.assertEqual(self.resolve(payload['id'], 'skip').status_code, 200)
        self.client.force_login(self.other_admin)
        self.assertEqual(self.resolve(payload['id'], 'approve').status_code, 409)
        decision = json.loads((self.root / 'decisions' / (payload['id'] + '.json')).read_text())
        self.assertEqual(decision['action'], 'skip')

    def test_failed_decision_delivery_is_persistent_and_retried(self):
        payload = self.movie_event()
        ingest()
        self.client.force_login(self.admin)
        with patch('portal.notifications.os.replace', side_effect=OSError('Disk unavailable')):
            response = self.resolve(payload['id'], 'approve')
        self.assertEqual(response.status_code, 202)
        value = Notification.objects.get()
        self.assertEqual((value.status, value.resolution), ('open', 'approve'))
        self.assertFalse((self.root / 'decisions' / (payload['id'] + '.json')).exists())
        self.client.get('/notifications/api', secure=True)
        value.refresh_from_db()
        self.assertEqual(value.status, 'resolved')
        self.assertTrue((self.root / 'decisions' / (payload['id'] + '.json')).exists())

    def test_non_cinema_sources_use_the_same_inbox_and_resolution_history(self):
        value = notify('documents', 'import-12', 'Import failed', 'Could not import the file', level='error')
        self.assertEqual(notify('documents', 'import-12', 'Duplicate', 'Duplicate').id, value.id)
        self.client.force_login(self.admin)
        self.assertEqual(self.resolve(value.id, 'approve').status_code, 400)
        self.assertEqual(self.resolve(value.id, 'resolve').status_code, 200)
        value.refresh_from_db()
        self.assertEqual(value.status, 'resolved')

    def test_csrf_and_post_are_required_for_resolution(self):
        value = notify('wiki', 'incident1', 'Notice', 'Needs attention')
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        url = '/notifications/' + value.id + '/resolve'
        self.assertEqual(client.get(url, secure=True).status_code, 405)
        self.assertEqual(client.post(url, {'action': 'resolve'}, secure=True).status_code, 403)
        token = client.get('/notifications/api', secure=True).json()['csrf_token']
        self.assertEqual(client.post(url, {'action': 'resolve'}, secure=True, HTTP_X_CSRFTOKEN=token,
                                     HTTP_ORIGIN='https://pivault.home.arpa').status_code, 302)

    def test_untrusted_urls_cannot_be_approved_through_a_forged_inbox_event(self):
        payload = self.movie_event()
        payload['details']['source_url'] = 'https://evil.example/movie.mp4'
        payload['id'] = hashlib.sha256(json.dumps(payload['details'], sort_keys=True).encode()).hexdigest()
        for path in (self.root / 'inbox').iterdir(): path.unlink()
        (self.root / 'inbox' / (payload['id'] + '.json')).write_text(json.dumps(payload))
        ingest()
        self.assertFalse(Notification.objects.exists())

    def test_review_template_escapes_notification_content(self):
        value = notify('wiki', 'incident1', '<script>alert(1)</script>', '<img src=x onerror=alert(1)>')
        self.client.force_login(self.admin)
        response = self.client.get('/notifications/' + value.id, secure=True)
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')

    def test_old_or_inactive_admin_session_is_not_authorized(self):
        self.client.force_login(self.admin)
        self.admin.is_active = False
        self.admin.save()
        self.assertEqual(self.client.get('/notifications/api', secure=True).status_code, 401)
