import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from portal import services_control
from portal.models import User

PROXY = 'http://docker-proxy:2375'


def inspect(status='running', health=None):
    state = {'Status': status}
    if health:
        state['Health'] = {'Status': health}
    return 200, json.dumps({'State': state}).encode()


@override_settings(PIVAULT_DOCKER_PROXY=PROXY)
class StateTests(TestCase):
    def test_running_container_is_running(self):
        with patch('portal.services_control._request', return_value=inspect('running', 'healthy')):
            self.assertEqual(services_control.state('books'), 'running')

    def test_unhealthy_or_restarting_container_needs_attention(self):
        with patch('portal.services_control._request', return_value=inspect('running', 'unhealthy')):
            self.assertEqual(services_control.state('books'), 'attention')
        with patch('portal.services_control._request', return_value=inspect('restarting')):
            self.assertEqual(services_control.state('books'), 'attention')

    def test_exited_or_missing_container_is_stopped(self):
        with patch('portal.services_control._request', return_value=inspect('exited')):
            self.assertEqual(services_control.state('books'), 'stopped')
        with patch('portal.services_control._request', return_value=(404, b'')):
            self.assertEqual(services_control.state('books'), 'stopped')

    def test_unreachable_proxy_is_unknown(self):
        with patch('portal.services_control._request', side_effect=services_control.ProxyError('down')):
            self.assertEqual(services_control.state('books'), 'unknown')

    def test_unconfigured_proxy_reports_unknown_without_calls(self):
        with override_settings(PIVAULT_DOCKER_PROXY=''):
            with patch('portal.services_control.urlopen') as urlopen:
                self.assertEqual(services_control.state('books'), 'unknown')
                urlopen.assert_not_called()


@override_settings(PIVAULT_DOCKER_PROXY=PROXY)
class ViewTests(TestCase):
    def make_user(self, role):
        return User.objects.create_user(username=role, email=f'{role}@example.com', display_name=role,
                                        role=role, service_access={})

    def test_status_requires_sign_in(self):
        self.assertEqual(self.client.get('/services/status', secure=True).status_code, 401)

    def test_status_reports_whether_the_viewer_can_control(self):
        with patch('portal.services_control._request', return_value=inspect('running')):
            self.client.force_login(self.make_user('user'))
            response = self.client.get('/services/status', secure=True).json()
            self.assertFalse(response['can_control'])
            self.assertEqual(set(response['services']), set(services_control.CONTAINERS))
            self.assertIn('csrf_token', response)
            self.client.force_login(self.make_user('admin'))
            self.assertTrue(self.client.get('/services/status', secure=True).json()['can_control'])

    def test_only_admins_can_act(self):
        self.client.force_login(self.make_user('user'))
        response = self.client.post('/services/books/restart', secure=True)
        self.assertEqual(response.status_code, 403)

    def test_admin_action_posts_to_the_listed_container_only(self):
        self.client.force_login(self.make_user('admin'))
        with patch('portal.services_control._request', return_value=(204, b'')) as request:
            response = self.client.post('/services/books/restart', secure=True)
        self.assertEqual(response.status_code, 200)
        request.assert_called_once_with('POST', '/containers/calibre-web/restart')

    def test_unknown_service_or_action_is_refused_without_a_proxy_call(self):
        self.client.force_login(self.make_user('admin'))
        with patch('portal.services_control._request') as request:
            self.assertEqual(self.client.post('/services/vaultwarden/stop', secure=True).status_code, 404)
            self.assertEqual(self.client.post('/services/books/exec', secure=True).status_code, 404)
            request.assert_not_called()

    def test_proxy_refusal_is_reported_as_a_bad_gateway(self):
        self.client.force_login(self.make_user('admin'))
        with patch('portal.services_control._request', side_effect=services_control.ProxyError('down')):
            response = self.client.post('/services/books/stop', secure=True)
        self.assertEqual(response.status_code, 502)
