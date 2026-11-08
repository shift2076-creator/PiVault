"""End-to-end enrollment: every requested account is provisioned, or none are kept."""
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from portal.integrations.native_http import CreatedAccount
from portal.invitations import issue
from portal.models import Invitation, User
from portal.runtime import PUBLIC_URLS
from portal.services import ADAPTERS

ACCOUNT_KEYS = ('music', 'videos', 'documents', 'photos', 'radicale', 'passwords')


class FakeAdapter:
    """Stands in for one native integration and records every provisioning call."""

    def __init__(self, key, modes, calls, fail=False):
        self.key = key
        self.modes = modes
        self.calls = calls
        self.fail = fail
        self.rollback = Mock(name=key + '.rollback')
        self.requires_browser_bundle = key == 'passwords'

    def credential_username(self, user):
        return user.email

    def provision(self, *, user, password, mode, bundle=None):
        self.calls.append(self.key)
        if self.fail:
            raise ValidationError(self.key + ' is unavailable.')
        return CreatedAccount(self.key, 'native-' + self.key, self.rollback)


class EnrollmentFlowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')
        self.calls = []
        self.adapters = {
            'music': FakeAdapter('music', ('shared', 'isolated'), self.calls),
            'videos': FakeAdapter('videos', ('shared',), self.calls),
            'documents': FakeAdapter('documents', ('isolated',), self.calls),
            'photos': FakeAdapter('photos', ('isolated',), self.calls),
            'radicale': FakeAdapter('radicale', ('isolated',), self.calls),
            'passwords': FakeAdapter('passwords', ('isolated',), self.calls),
        }
        for key in ACCOUNT_KEYS:
            patcher = patch.dict(PUBLIC_URLS, {key: 'https://192.168.4.90:8443/' + key})
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(ADAPTERS, self.adapters)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The browser-side vault bundle is checked by its own tests; here it is a fixed stand-in.
        patcher = patch('portal.vault_views.join_bundle', return_value={'items': {}})
        patcher.start()
        self.addCleanup(patcher.stop)

    def enroll(self, code, email='alex@example.com'):
        return self.client.post('/join', {'code': code, 'email': email,
            'password': 'A-long-new-passphrase-482!', 'password_confirm': 'A-long-new-passphrase-482!'},
            secure=True)

    def test_every_requested_account_is_provisioned_once_in_order(self):
        _, code = issue('Alex', 'user', {'music': 'shared', 'documents': 'isolated', 'photos': 'isolated',
                                          'radicale': 'isolated', 'passwords': 'isolated'}, self.admin)
        response = self.enroll(code)
        self.assertEqual(response.status_code, 302)
        # Password Manager goes last because its encrypted entries need the other accounts first.
        self.assertEqual(self.calls, ['music', 'videos', 'documents', 'photos', 'radicale', 'passwords'])
        user = User.objects.get(email='alex@example.com')
        self.assertEqual(user.service_access, {'books': 'shared', 'games': 'shared', 'music': 'shared',
            'videos': 'shared', 'documents': 'isolated', 'wiki': 'shared', 'passwords': 'isolated',
            'photos': 'isolated', 'radicale': 'isolated'})
        self.assertTrue(Invitation.objects.get().used_at)
        for key in ACCOUNT_KEYS:
            self.adapters[key].rollback.assert_not_called()

    def test_a_declined_service_is_never_provisioned(self):
        _, code = issue('Alex', 'user', {'music': 'none', 'videos': 'none', 'documents': 'none',
                                          'photos': 'none', 'radicale': 'none'}, self.admin)
        self.assertEqual(self.enroll(code).status_code, 302)
        # Password Manager is always per user, so it is provisioned whatever is posted.
        self.assertEqual(self.calls, ['passwords'])
        user = User.objects.get(email='alex@example.com')
        self.assertEqual(user.service_access['music'], 'none')
        self.assertEqual(user.service_access['videos'], 'none')
        self.assertEqual(user.service_access['radicale'], 'none')

    def test_failure_partway_rolls_back_earlier_accounts_and_releases_the_code(self):
        self.adapters['photos'].fail = True
        _, code = issue('Alex', 'user', {'music': 'shared', 'documents': 'isolated', 'photos': 'isolated',
                                          'radicale': 'isolated', 'passwords': 'isolated'}, self.admin)
        with self.assertLogs('portal.setup', level='WARNING'):
            response = self.enroll(code)
        self.assertEqual(response.status_code, 400)
        # Passwords was never reached; accounts created before the failure are removed.
        self.assertEqual(self.calls, ['music', 'videos', 'documents', 'photos'])
        self.adapters['music'].rollback.assert_called_once()
        self.adapters['documents'].rollback.assert_called_once()
        self.assertFalse(User.objects.filter(email='alex@example.com').exists())
        self.assertIsNone(Invitation.objects.get().used_at)
        self.assertEqual(self.enroll(code).status_code, 400)  # Retrying still fails until the admin fixes it.
