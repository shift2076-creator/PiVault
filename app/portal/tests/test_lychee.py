from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from portal.integrations.lychee import LycheeAdapter
from portal.integrations.native_http import NativeError
from portal.models import User

TOKEN = 'admin-token-for-tests'


class FakeLychee:
    """Stands in for Lychee's admin API. Records every call and its bearer token."""

    def __init__(self, rows=None, fail=False):
        self.rows = [dict(r) for r in (rows or [])]
        self.fail = fail
        self.calls = []

    def factory(self):
        fake = self

        class Client:
            def __init__(self, service, base_url, headers=None):
                self.headers = dict(headers or {})

            def request(self, method, path, data=None):
                fake.calls.append((method, path, self.headers.get('Authorization')))
                if fake.fail:
                    raise NativeError('Photos')
                return {'data': [dict(r) for r in fake.rows]}

        return Client


class LycheeAdapterTests(TestCase):
    """Photos is shared or none: one Lychee account for everyone, never a per-person account."""

    def setUp(self):
        self.fake = FakeLychee(rows=[{'id': 1, 'username': 'shared-photos@example.com', 'may_administrate': False}])
        self.adapter = LycheeAdapter('https://photos.example', TOKEN, client_factory=self.fake.factory())
        self.user = SimpleNamespace(email='Sam@Example.com', display_name='Sam', role='user')

    def test_list_accounts_uses_the_admin_token_and_returns_usernames(self):
        self.assertEqual(self.adapter.list_accounts(), ['shared-photos@example.com'])
        self.assertEqual(self.fake.calls, [('GET', '/api/v2/UserManagement', 'Bearer ' + TOKEN)])

    def test_shared_provision_creates_no_account(self):
        self.assertIsNone(self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='shared'))
        self.assertEqual(self.fake.calls, [])

    def test_per_user_mode_is_refused(self):
        with self.assertRaisesMessage(ValidationError, 'shared or none'):
            self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertEqual(self.fake.calls, [])

    def test_unreadable_account_list_is_reported(self):
        self.fake.fail = True
        with self.assertRaises(NativeError):
            self.adapter.list_accounts()

    def test_photos_row_offers_shared_or_none_once_configured(self):
        from portal.services import ADAPTERS
        admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')
        self.client.force_login(admin)
        with patch.dict(ADAPTERS, {'photos': self.adapter}):
            markup = self.client.get('/users/add', secure=True).content.decode()
        row = markup.split('data-service="photos"', 1)[1].split('</div>', 1)[0]
        self.assertIn('value="shared"', row)
        self.assertNotIn('value="isolated"', row)
