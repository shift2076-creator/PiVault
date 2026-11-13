import os
import re
import tempfile
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from portal.integrations.native_http import NativeError
from portal.integrations.navidrome import NavidromeAdapter, NavidromeCleanupRequired

ADMIN = 'nd-admin'
ADMIN_PASSWORD = 'admin-pass-for-tests'


class FakeNavidrome:
    """In-memory stand-in for the Navidrome 0.64 REST routes the adapter uses."""

    def __init__(self, libraries):
        self.libraries = {i: {'id': i, 'name': n, 'path': p} for i, (n, p) in libraries.items()}
        self.users = {}
        self.user_libraries = {}
        self.calls = []
        self.fail = {}
        self._next_user = 100
        self._next_library = 50

    def factory(self, service, base_url, headers):
        return FakeClient(self)

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        for (fail_method, fail_path), error in self.fail.items():
            if method == fail_method and path.startswith(fail_path):
                raise error
        if method == 'POST' and path == '/auth/login':
            return self._login(data)
        if path == '/api/user' and method == 'GET':
            return [{'id': uid, 'userName': u['userName'], 'isAdmin': u['isAdmin']}
                    for uid, u in self.users.items()]
        if path == '/api/user' and method == 'POST':
            uid = str(self._next_user)
            self._next_user += 1
            self.users[uid] = {'userName': data['userName'], 'password': data['password'],
                               'isAdmin': data['isAdmin']}
            self.user_libraries[uid] = set()
            return {'id': uid, 'userName': data['userName'], 'isAdmin': data['isAdmin']}
        if path == '/api/library' and method == 'GET':
            return list(self.libraries.values())
        if path == '/api/library' and method == 'POST':
            lid = self._next_library
            self._next_library += 1
            self.libraries[lid] = {'id': lid, 'name': data['name'], 'path': data['path']}
            return self.libraries[lid]
        match = re.fullmatch(r'/api/library/(\d+)', path)
        if match and method == 'DELETE':
            if int(match.group(1)) not in self.libraries:
                raise NativeError('Music', status=404)
            del self.libraries[int(match.group(1))]
            for ids in self.user_libraries.values():
                ids.discard(int(match.group(1)))
            return None
        match = re.fullmatch(r'/api/user/([^/]+)', path)
        if match:
            uid = match.group(1)
            if uid not in self.users:
                raise NativeError('Music', status=404)
            if method == 'GET':
                u = self.users[uid]
                return {'id': uid, 'userName': u['userName'], 'isAdmin': u['isAdmin']}
            if method == 'DELETE':
                del self.users[uid]
                del self.user_libraries[uid]
                return None
        match = re.fullmatch(r'/api/user/([^/]+)/library', path)
        if match:
            uid = match.group(1)
            if method == 'PUT':
                self.user_libraries[uid] = set(data['libraryIds'])
                return None
            if method == 'GET':
                return [{'id': i} for i in sorted(self.user_libraries[uid])]
        raise AssertionError(f'Unexpected Navidrome request: {method} {path}')

    def _login(self, data):
        if data['username'] == ADMIN and data['password'] == ADMIN_PASSWORD:
            return {'isAdmin': True, 'token': 'token-for-tests'}
        for uid, user in self.users.items():
            if user['userName'] == data['username'] and user['password'] == data['password']:
                return {'id': uid, 'isAdmin': user['isAdmin']}
        raise NativeError('Music', status=401)


class FakeClient:
    def __init__(self, state):
        self.state = state

    def request(self, method, path, data=None):
        return self.state.request(method, path, data)


def make_adapter(state, *, private_root=None, library_ids=(1,)):
    return NavidromeAdapter('http://navidrome:4533', ADMIN, ADMIN_PASSWORD,
                            library_ids=list(library_ids) if library_ids is not None else None,
                            private_root=private_root, client_factory=state.factory)


def person(email, role='user'):
    return SimpleNamespace(email=email, display_name='Alex', role=role)


def posted(state, path):
    return [data for method, p, data in state.calls if method == 'POST' and p == path]


class NavidromeSharedAccessTests(SimpleTestCase):
    def test_without_private_root_only_shared_access_is_offered(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        adapter = make_adapter(state, library_ids=None)
        self.assertEqual(adapter.modes, ('shared',))
        with self.assertRaises(ValidationError):
            adapter.provision(user=person('alex@example.com'), password='pw', mode='isolated')
        self.assertEqual(state.calls, [])

    def test_shared_access_creates_no_per_person_account(self):
        with tempfile.TemporaryDirectory() as root:
            state = FakeNavidrome({1: ('Music', '/music'), 2: ('Private: other', root + '/other-1234')})
            adapter = make_adapter(state, private_root=root, library_ids=[1])
            receipt = adapter.provision(user=person('alex@example.com'), password='pw', mode='shared')
            # The one shared login is copied into each vault instead, so nothing is created and nothing is returned.
            self.assertIsNone(receipt)
            self.assertEqual(state.calls, [])

    def test_private_root_requires_explicit_shared_library_ids(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        with self.assertRaises(ValueError):
            make_adapter(state, private_root='/music-private', library_ids=None)

    def test_private_root_must_be_a_clean_absolute_path(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        for bad in ('music-private', '/music-private/', '/a/../b', '/a//b', '/a/./b'):
            with self.subTest(root=bad), self.assertRaises(ValueError):
                make_adapter(state, private_root=bad)


class NavidromePrivateAccessTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_isolated_user_gets_one_private_library_and_a_working_password(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        adapter = make_adapter(state, private_root=self.root)
        receipt = adapter.provision(user=person('Alex+Music@Example.com'), password='user-pw', mode='isolated')
        library_post = posted(state, '/api/library')[0]
        self.assertEqual(library_post['name'], 'Private: alex+music@example.com')
        folder = library_post['path']
        self.assertTrue(folder.startswith(self.root + '/alex-music-example.com-'))
        self.assertTrue(os.path.isdir(folder))
        self.assertEqual(state.user_libraries[receipt.native_id], {50})
        self.assertEqual(state.users[receipt.native_id]['password'], 'user-pw')

    def test_admin_role_gets_a_normal_navidrome_account(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        adapter = make_adapter(state, private_root=self.root)
        receipt = adapter.provision(user=person('boss@example.com', role='admin'), password='pw', mode='isolated')
        self.assertFalse(state.users[receipt.native_id]['isAdmin'])

    def test_non_empty_private_folder_is_never_reused(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        adapter = make_adapter(state, private_root=self.root)
        email = 'alex@example.com'
        folder = os.path.join(self.root, 'alex-example.com-' + __import__('hashlib').sha256(email.encode()).hexdigest()[:10])
        os.makedirs(folder)
        open(os.path.join(folder, 'old-song.mp3'), 'w').close()
        with self.assertRaises(ValidationError):
            adapter.provision(user=person(email), password='pw', mode='isolated')
        self.assertEqual(posted(state, '/api/library'), [])
        self.assertEqual(state.users, {})

    def test_missing_private_storage_is_refused(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        adapter = make_adapter(state, private_root=os.path.join(self.root, 'absent'))
        with self.assertRaises(ValidationError):
            adapter.provision(user=person('alex@example.com'), password='pw', mode='isolated')
        self.assertEqual([c for c in state.calls if c[0] != 'GET' and c[1] != '/auth/login'], [])

    def test_failed_user_creation_removes_the_new_private_library(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        state.fail[('POST', '/api/user')] = NativeError('Music', status=400)
        adapter = make_adapter(state, private_root=self.root)
        with self.assertRaises(NativeError):
            adapter.provision(user=person('alex@example.com'), password='pw', mode='isolated')
        self.assertEqual(state.libraries.keys(), {1})

    def test_receipt_rollback_removes_user_and_private_library(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        adapter = make_adapter(state, private_root=self.root)
        receipt = adapter.provision(user=person('alex@example.com'), password='pw', mode='isolated')
        receipt.rollback()
        self.assertEqual(state.users, {})
        self.assertEqual(state.libraries.keys(), {1})

    def test_uncertain_library_creation_asks_an_admin_to_clean_up(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        state.fail[('POST', '/api/library')] = NativeError('Music', status=503, uncertain=True)
        adapter = make_adapter(state, private_root=self.root)
        with self.assertRaises(NavidromeCleanupRequired) as caught:
            adapter.provision(user=person('alex@example.com'), password='pw', mode='isolated')
        self.assertEqual(caught.exception.cleanup_failures,
                         [{'service': 'music', 'native_id': 'library:alex@example.com'}])
        self.assertEqual(state.users, {})

    def test_failed_library_removal_is_reported_without_credentials(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        state.fail[('POST', '/api/user')] = NativeError('Music', status=400)
        state.fail[('DELETE', '/api/library/')] = NativeError('Music', status=503)
        adapter = make_adapter(state, private_root=self.root)
        with self.assertRaises(NavidromeCleanupRequired) as caught:
            adapter.provision(user=person('alex@example.com'), password='DO_NOT_REPORT', mode='isolated')
        self.assertEqual(caught.exception.cleanup_failures,
                         [{'service': 'music', 'native_id': 'library:50'}])
        self.assertNotIn('DO_NOT_REPORT', str(caught.exception))
