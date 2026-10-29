from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.test import Client, TestCase
from portal.client_packages import maintain
from portal.models import Notification, SiteSettings, User


class OfflineModeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')

    def test_default_is_off_and_admin_can_switch_it_both_ways(self):
        self.assertFalse(SiteSettings.is_offline())
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/system', secure=True), 'Turn offline mode on')
        self.assertNotContains(self.client.get('/users/add', secure=True), 'Turn offline mode')
        response = self.client.post('/users/offline-mode', {'offline': 'on'}, secure=True)
        self.assertRedirects(response, '/system', fetch_redirect_response=False)
        self.assertTrue(SiteSettings.is_offline())
        self.assertContains(self.client.get('/system', secure=True), 'Turn offline mode off')
        self.client.post('/users/offline-mode', {'offline': 'off'}, secure=True)
        self.assertFalse(SiteSettings.is_offline())

    def test_system_page_is_admin_only(self):
        self.assertEqual(self.client.get('/system', secure=True).status_code, 302)
        person = User.objects.create_user(username='person', email='person@example.com',
            display_name='Person', role='user')
        self.client.force_login(person)
        self.assertEqual(self.client.get('/system', secure=True).status_code, 403)

    def test_only_admins_can_switch_and_csrf_is_required(self):
        person = User.objects.create_user(username='person', email='person@example.com',
            display_name='Person', role='user')
        self.client.force_login(person)
        self.assertEqual(self.client.post('/users/offline-mode', {'offline': 'on'}, secure=True).status_code, 403)
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.admin)
        self.assertEqual(guarded.post('/users/offline-mode', {'offline': 'on'}, secure=True).status_code, 403)
        self.assertEqual(self.client.get('/users/offline-mode', secure=True).status_code, 405)
        self.assertFalse(SiteSettings.is_offline())

    def test_offline_maintenance_makes_no_internet_calls_and_no_notices(self):
        SiteSettings.set_offline(True)
        with patch('portal.client_packages.discover') as discover, \
             patch('portal.client_packages.http_open') as download:
            self.assertEqual(maintain(), [])
            output = StringIO()
            call_command('maintain_clients', stdout=output)
        discover.assert_not_called()
        download.assert_not_called()
        self.assertIn('Offline mode is on', output.getvalue())
        self.assertFalse(Notification.objects.exists())

    def test_command_reports_and_sets_state_for_the_nightly_job(self):
        def run(*args):
            output = StringIO()
            call_command('offline_mode', *args, stdout=output)
            return output.getvalue().strip()
        self.assertEqual(run('--status'), 'off')
        self.assertEqual(run('--on'), 'on')
        self.assertEqual(run(), 'on')
        self.assertEqual(run('--off'), 'off')
