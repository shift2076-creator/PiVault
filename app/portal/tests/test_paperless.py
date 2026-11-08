import re
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from portal.integrations.native_http import NativeError
from portal.integrations.paperless import PaperlessAdapter, PaperlessCleanupRequired, USER_PERMISSIONS

TOKEN = 'provisioning-token-for-tests'


class FakePaperless:
    """In-memory stand-in for the Paperless-ngx 3.3.3 user routes the adapter uses."""

    def __init__(self):
        self.users = {}
        self.calls = []
        self.fail = {}
        self.read_back_permissions = None
        self._next = 10

    def factory(self, service, base_url, headers):
        return FakeClient(self, headers)

    def request(self, method, path, data=None, headers=None):
        self.calls.append((method, path, data))
        for (fail_method, fail_path), error in self.fail.items():
            if method == fail_method and path.startswith(fail_path):
                raise error
        if method == 'POST' and path == '/api/token/':
            for user in self.users.values():
                if user['username'] == data['username'] and user['password'] == data['password']:
                    return {'token': 'user-token'}
            raise NativeError('Documents', status=400)
        if (headers or {}).get('Authorization') != 'Token ' + TOKEN:
            raise NativeError('Documents', status=401)
        if method == 'GET' and path.startswith('/api/users/?'):
            return {'count': len(self.users), 'results': list(self.users.values())}
        if method == 'POST' and path == '/api/users/':
            uid = self._next
            self._next += 1
            self.users[str(uid)] = {'id': uid, 'username': data['username'], 'password': data['password'],
                                    'is_superuser': data['is_superuser'],
                                    'user_permissions': list(data['user_permissions'])}
            return {'id': uid, 'username': data['username']}
        match = _user_path(path)
        if match and match in self.users:
            if method == 'GET':
                user = dict(self.users[match])
                if self.read_back_permissions is not None:
                    user['user_permissions'] = self.read_back_permissions
                return user
            if method == 'DELETE':
                del self.users[match]
                return None
        raise AssertionError(f'Unexpected Paperless request: {method} {path}')


def _user_path(path):
    match = re.fullmatch(r'/api/users/(\d+)/', path)
    return match.group(1) if match else None


class FakeClient:
    def __init__(self, state, headers):
        self.state = state
        self.headers = headers

    def request(self, method, path, data=None):
        return self.state.request(method, path, data, self.headers)


def make_adapter(state):
    return PaperlessAdapter('http://paperless:8000', TOKEN, client_factory=state.factory)


def person(email, role='user'):
    return SimpleNamespace(email=email, display_name='Alex', role=role)


class PaperlessProvisioningTests(SimpleTestCase):
    def test_ordinary_user_gets_standard_document_permissions_and_a_working_login(self):
        state = FakePaperless()
        receipt = make_adapter(state).provision(user=person('Alex@Example.com'), password='user-pw', mode='isolated')
        created = state.users[receipt.native_id]
        self.assertEqual(created['username'], 'alex@example.com')
        self.assertFalse(created['is_superuser'])
        self.assertEqual(sorted(created['user_permissions']), sorted(USER_PERMISSIONS))
        self.assertIn(('POST', '/api/token/', {'username': 'alex@example.com', 'password': 'user-pw'}), state.calls)

    def test_shared_and_isolated_both_create_the_same_standard_account(self):
        for mode in ('shared', 'isolated'):
            with self.subTest(mode=mode):
                state = FakePaperless()
                receipt = make_adapter(state).provision(user=person('alex@example.com'), password='pw', mode=mode)
                self.assertEqual(sorted(state.users[receipt.native_id]['user_permissions']), sorted(USER_PERMISSIONS))

    def test_admin_role_maps_to_superuser_without_extra_permissions(self):
        state = FakePaperless()
        receipt = make_adapter(state).provision(user=person('boss@example.com', role='admin'), password='pw', mode='isolated')
        created = state.users[receipt.native_id]
        self.assertTrue(created['is_superuser'])
        self.assertEqual(created['user_permissions'], [])

    def test_existing_account_is_never_adopted(self):
        state = FakePaperless()
        state.users['1'] = {'id': 1, 'username': 'alex@example.com', 'password': 'other', 'is_superuser': False,
                            'user_permissions': []}
        with self.assertRaises(ValidationError):
            make_adapter(state).provision(user=person('ALEX@example.com'), password='pw', mode='isolated')
        self.assertEqual([c for c in state.calls if c[0] == 'POST'], [])

    def test_mismatched_permissions_are_rolled_back(self):
        state = FakePaperless()
        state.read_back_permissions = ['documents.view_document']
        with self.assertRaises(NativeError):
            make_adapter(state).provision(user=person('alex@example.com'), password='pw', mode='isolated')
        self.assertEqual(state.users, {})

    def test_failed_password_check_removes_the_new_account(self):
        state = FakePaperless()
        state.fail[('POST', '/api/token/')] = NativeError('Documents', status=500, uncertain=True)
        with self.assertRaises(NativeError):
            make_adapter(state).provision(user=person('alex@example.com'), password='pw', mode='isolated')
        self.assertEqual(state.users, {})

    def test_uncertain_creation_asks_for_admin_cleanup_without_guessing_an_id(self):
        state = FakePaperless()
        state.fail[('POST', '/api/users/')] = NativeError('Documents', status=503, uncertain=True)
        with self.assertRaises(PaperlessCleanupRequired) as caught:
            make_adapter(state).provision(user=person('alex@example.com'), password='DO_NOT_REPORT', mode='isolated')
        self.assertEqual(caught.exception.cleanup_failures,
                         [{'service': 'documents', 'native_id': 'username:alex@example.com'}])
        self.assertNotIn('DO_NOT_REPORT', str(caught.exception))
        self.assertEqual(state.users, {})

    def test_receipt_rollback_removes_the_account(self):
        state = FakePaperless()
        receipt = make_adapter(state).provision(user=person('alex@example.com'), password='pw', mode='isolated')
        receipt.rollback()
        self.assertEqual(state.users, {})

    def test_empty_provisioning_token_is_refused(self):
        with self.assertRaises(ValueError):
            PaperlessAdapter('http://paperless:8000', '   ', client_factory=FakePaperless().factory)
