import json
import urllib.error
from unittest.mock import patch

from django.test import TestCase, override_settings
from portal import service_control
from portal.models import User


def _reply(body=b'', status=200):
    class Reply:
        def __init__(self):
            self.status = status

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    return Reply()


@override_settings(SECURE_SSL_REDIRECT=False)
class StatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', email='user@example.com', password='pw-long-enough-1', role='user')
        self.admin = User.objects.create_user(username='admin', email='admin@example.com', password='pw-long-enough-1', role='admin')

    def test_requires_sign_in(self):
        response = self.client.get('/services/status')
        self.assertEqual(response.status_code, 401)

    def test_user_sees_states_without_control(self):
        self.client.force_login(self.user)
        with patch.object(service_control, '_state', return_value='running'):
            response = self.client.get('/services/status')
        data = response.json()
        self.assertFalse(data['can_control'])
        self.assertNotIn('csrf_token', data)
        self.assertEqual(set(data['services']), set(service_control.CONTAINERS))

    def test_admin_gets_csrf_token(self):
        self.client.force_login(self.admin)
        with patch.object(service_control, '_state', return_value='stopped'):
            data = self.client.get('/services/status').json()
        self.assertTrue(data['can_control'])
        self.assertTrue(data['csrf_token'])

    def test_state_mapping(self):
        def reply(state, health=None):
            info = {'State': {'Status': state}}
            if health:
                info['State']['Health'] = {'Status': health}
            return _reply(json.dumps(info).encode())
        with patch.object(service_control, '_proxy', return_value=(200, reply('running').read())):
            self.assertEqual(service_control._state('x'), 'running')
        with patch.object(service_control, '_proxy', return_value=(200, b'{"State":{"Status":"running","Health":{"Status":"unhealthy"}}}')):
            self.assertEqual(service_control._state('x'), 'attention')
        with patch.object(service_control, '_proxy', return_value=(200, b'{"State":{"Status":"exited"}}')):
            self.assertEqual(service_control._state('x'), 'stopped')
        with patch.object(service_control, '_proxy', side_effect=OSError):
            self.assertEqual(service_control._state('x'), 'unknown')


@override_settings(SECURE_SSL_REDIRECT=False)
class ControlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user2', email='user2@example.com', password='pw-long-enough-1', role='user')
        self.admin = User.objects.create_user(username='admin2', email='admin2@example.com', password='pw-long-enough-1', role='admin')

    def test_user_gets_403(self):
        self.client.force_login(self.user)
        with patch.object(service_control, '_proxy') as proxy:
            response = self.client.post('/services/books/restart', enforce_csrf_checks=False)
        self.assertEqual(response.status_code, 403)
        proxy.assert_not_called()

    def test_admin_restart_calls_listed_container_only(self):
        self.client.force_login(self.admin)
        with patch.object(service_control, '_proxy', return_value=(204, b'')) as proxy:
            response = self.client.post('/services/books/restart', enforce_csrf_checks=False)
        self.assertEqual(response.status_code, 200)
        proxy.assert_called_once_with('POST', '/containers/calibre-web/restart')

    def test_unknown_key_or_verb_is_refused(self):
        self.client.force_login(self.admin)
        with patch.object(service_control, '_proxy') as proxy:
            self.assertEqual(self.client.post('/services/nope/restart', enforce_csrf_checks=False).status_code, 404)
            self.assertEqual(self.client.post('/services/books/exec', enforce_csrf_checks=False).status_code, 404)
        proxy.assert_not_called()

    def test_already_in_state_counts_as_success(self):
        self.client.force_login(self.admin)
        error = urllib.error.HTTPError('u', 304, 'Not Modified', {}, None)
        with patch.object(service_control, '_proxy', side_effect=error):
            response = self.client.post('/services/music/start', enforce_csrf_checks=False)
        self.assertEqual(response.status_code, 200)

    def test_proxy_failure_returns_502(self):
        self.client.force_login(self.admin)
        with patch.object(service_control, '_proxy', side_effect=OSError):
            response = self.client.post('/services/music/stop', enforce_csrf_checks=False)
        self.assertEqual(response.status_code, 502)
