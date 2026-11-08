from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from portal.integrations.lychee import LycheeAdapter, LycheeCleanupRequired
from portal.integrations.native_http import NativeError
from portal.models import User

TOKEN = 'admin-token-for-tests'


class FakeLychee:
    """Stands in for Lychee's admin API. Records every call and its bearer token."""

    def __init__(self, rows=None, fail_login=False, fail_create=None):
        self.rows = [dict(r) for r in (rows or [])]
        self.passwords = {}
        self.calls = []
        self.next_id = 7
        self.fail_login = fail_login
        self.fail_create = fail_create

    def factory(self):
        fake = self

        class Client:
            def __init__(self, service, base_url, headers=None):
                self.headers = dict(headers or {})

            def request(self, method, path, data=None):
                fake.calls.append((method, path, self.headers.get('Authorization')))
                if method == 'GET' and path == '/api/v2/UserManagement':
                    return {'data': [dict(r) for r in fake.rows]}
                if method == 'POST' and path == '/api/v2/UserManagement':
                    if fake.fail_create:
                        raise fake.fail_create
                    uid = fake.next_id
                    fake.next_id += 1
                    fake.passwords[data['username']] = data['password']
                    fake.rows.append({'id': uid, 'username': data['username'], 'may_administrate': False})
                    return {'data': {'id': uid, 'username': data['username']}}
                if method == 'DELETE' and path == '/api/v2/UserManagement':
                    fake.rows = [r for r in fake.rows if r['id'] != data['id']]
                    return None
                if method == 'POST' and path == '/api/v2/Auth::login':
                    if fake.fail_login or fake.passwords.get(data['username']) != data['password']:
                        raise NativeError('Photos', status=401)
                    return None
                raise AssertionError(f'unexpected {method} {path}')

        return Client


class LycheeAdapterTests(TestCase):
    def setUp(self):
        self.fake = FakeLychee(rows=[{'id': 1, 'username': 'owner', 'may_administrate': True}])
        self.adapter = LycheeAdapter('http://lychee:8000', TOKEN, client_factory=self.fake.factory())
        self.user = SimpleNamespace(email='Sam@Example.com', role='user')

    def test_provision_creates_a_non_admin_account_with_the_admin_token(self):
        receipt = self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertEqual(receipt.service, 'photos')
        self.assertEqual(receipt.native_id, '7')
        self.assertIn(('POST', '/api/v2/UserManagement', 'Bearer ' + TOKEN), self.fake.calls)
        created = [r for r in self.fake.rows if r['id'] == 7][0]
        self.assertEqual(created['username'], 'sam@example.com')
        self.assertIs(created['may_administrate'], False)

    def test_password_is_verified_by_logging_in_as_the_new_user(self):
        self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertIn(('POST', '/api/v2/Auth::login', None), self.fake.calls)

    def test_rollback_deletes_only_the_new_account(self):
        receipt = self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        receipt.rollback()
        self.assertEqual([r['id'] for r in self.fake.rows], [1])

    def test_failed_login_check_deletes_the_new_account(self):
        self.fake.fail_login = True
        with self.assertRaises(NativeError):
            self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertEqual([r['id'] for r in self.fake.rows], [1])

    def test_duplicate_username_is_rejected_before_creating(self):
        self.fake.rows.append({'id': 3, 'username': 'sam@example.com', 'may_administrate': False})
        with self.assertRaisesMessage(ValidationError, 'already uses that email'):
            self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertFalse([c for c in self.fake.calls if c[0] == 'POST'])

    def test_lost_create_response_leaves_cleanup_for_an_admin(self):
        self.fake.fail_create = NativeError('Photos', status=503, uncertain=True)
        with self.assertRaises(LycheeCleanupRequired):
            self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')

    def test_only_per_user_mode_is_supported(self):
        with self.assertRaisesMessage(ValidationError, 'does not support that access mode'):
            self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='shared')

    def test_admin_token_is_required(self):
        with self.assertRaises(ValueError):
            LycheeAdapter('http://lychee:8000', '  ')


class LycheeAddUserRowTests(TestCase):
    def test_photos_offers_per_user_once_configured_and_never_shared(self):
        from portal.services import ADAPTERS
        admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')
        self.client.force_login(admin)
        adapter = LycheeAdapter('http://lychee:8000', TOKEN, client_factory=FakeLychee().factory())
        with patch.dict(ADAPTERS, {'photos': adapter}):
            markup = self.client.get('/users/add', secure=True).content.decode()
        photos = markup.split('data-service="photos"', 1)[1].split('</div>', 1)[0]
        self.assertIn('value="isolated"', photos)
        self.assertIn('value="none"', photos)
        self.assertNotIn('value="shared"', photos)
        self.assertIn('not private from Lychee administrators', photos)
