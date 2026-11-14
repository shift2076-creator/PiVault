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
        self.assertFalse(self.client.get('/notifications/api', secure=True).json()['offline'])
        response = self.client.post('/users/offline-mode', {'offline': 'on'}, secure=True)
        self.assertEqual(response.json(), {'offline': True})
        self.assertTrue(SiteSettings.is_offline())
        self.assertTrue(self.client.get('/notifications/api', secure=True).json()['offline'])
        response = self.client.post('/users/offline-mode', {'offline': 'off'}, secure=True)
        self.assertEqual(response.json(), {'offline': False})
        self.assertFalse(SiteSettings.is_offline())

    def test_system_page_is_gone_and_the_control_is_in_the_admin_bell(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/system', secure=True).status_code, 404)
        self.assertNotContains(self.client.get('/certificate', secure=True), 'Offline mode')
        script = self.client.get('/notifications/bell.js', secure=True).content.decode()
        self.assertIn("'/users/offline-mode'", script)
        self.assertIn("'OFFLINE'", script)

    def test_only_admins_can_switch_and_csrf_is_required(self):
        person = User.objects.create_user(username='person', email='person@example.com',
            display_name='Person', role='user')
        self.client.force_login(person)
        self.assertEqual(self.client.post('/users/offline-mode', {'offline': 'on'}, secure=True).status_code, 403)
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.admin)
        self.assertEqual(guarded.post('/users/offline-mode', {'offline': 'on'}, secure=True).status_code, 403)
        self.client.force_login(self.admin)
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

    def test_a_failing_client_check_does_not_fail_the_nightly_run(self):
        import tempfile
        from django.test import override_settings
        from django.core.exceptions import ValidationError
        with tempfile.TemporaryDirectory() as root, override_settings(PIVAULT_CLIENT_ROOT=root), \
             patch('portal.client_packages.discover', side_effect=ValidationError('check failed')):
            output = StringIO()
            call_command('maintain_clients', stdout=output)
        self.assertIn('Client checks needing review', output.getvalue())
        self.assertTrue(Notification.objects.filter(source='clients', title__endswith='release check failed').exists())
        self.assertFalse(Notification.objects.filter(title='Nightly client downloads needs review').exists())

    def test_command_reports_and_sets_state_for_the_nightly_job(self):
        def run(*args):
            output = StringIO()
            call_command('offline_mode', *args, stdout=output)
            return output.getvalue().strip()
        self.assertEqual(run('--status'), 'off')
        self.assertEqual(run('--on'), 'on')
        self.assertEqual(run(), 'on')
        self.assertEqual(run('--off'), 'off')
