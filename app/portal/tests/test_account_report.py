import os
import tempfile
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from portal.account_report import build_report, format_report
from portal.integrations.jellyfin import JellyfinAdapter
from portal.integrations.lychee import LycheeAdapter
from portal.integrations.navidrome import NavidromeAdapter
from portal.integrations.paperless import PaperlessAdapter
from portal.integrations.radicale import RadicaleAdapter
from portal.models import User
from portal.services import ADAPTERS
from portal.tests.test_navidrome import ADMIN, ADMIN_PASSWORD, FakeNavidrome


class FakeAccounts:
    """Stands in for a configured adapter: only the two methods the report uses."""

    def __init__(self, names=(), error=None):
        self.names = list(names)
        self.error = error

    def credential_username(self, user):
        return user.email.strip().lower()

    def list_accounts(self):
        if self.error is not None:
            raise self.error
        return list(self.names)


class FakeRows:
    """Returns canned JSON for each GET path, the way the adapters' clients do."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, data=None):
        self.calls.append((method, path))
        return self.responses[path]

    def factory(self, service, base_url, headers=None):
        return self


class AccountReportTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create(
            username='alice', email='alice@example.com', display_name='Alice', role='user',
            service_access={'music': 'shared', 'books': 'shared', 'photos': 'none'})
        self.bob = User.objects.create(
            username='bob', email='bob@example.com', display_name='Bob', role='user',
            service_access={'music': 'none'})

    def run_report(self, adapters):
        with mock.patch.dict(ADAPTERS, adapters, clear=True):
            return build_report()

    def section(self, report, key):
        return next(s for s in report['services'] if s['key'] == key)

    def test_lists_native_accounts_and_flags_a_user_without_one(self):
        report = self.run_report({'music': FakeAccounts(names=['someone-else'])})
        self.assertEqual(self.section(report, 'music')['accounts'], ['someone-else'])
        self.assertEqual(report['problems'], [
            'MISSING  Music: PiVault user alice@example.com expects an account named alice@example.com',
            'UNMATCHED  Music: account someone-else matches no PiVault user',
        ])

    def test_matching_account_produces_no_problem(self):
        report = self.run_report({'music': FakeAccounts(names=['Alice@Example.com'])})
        self.assertEqual(report['problems'], [])

    def test_leftover_test_account_is_reported_as_unmatched(self):
        report = self.run_report({'music': FakeAccounts(names=['alice@example.com', 'test-fixture'])})
        self.assertEqual(report['problems'], ['UNMATCHED  Music: account test-fixture matches no PiVault user'])

    def test_one_unreadable_service_does_not_stop_the_others(self):
        report = self.run_report({
            'music': FakeAccounts(error=ValidationError('Music is down.')),
            'books': FakeAccounts(names=[]),
        })
        self.assertEqual(self.section(report, 'music')['error'], 'Music is down.')
        # The Books check still ran after Music failed, and found alice has no Books account.
        self.assertEqual(report['problems'], [
            'MISSING  Books: PiVault user alice@example.com expects an account named alice@example.com',
        ])
        self.assertIn('COULD NOT READ (Music is down.)', format_report(report))

    def test_open_services_without_an_adapter_are_not_missing_accounts(self):
        report = self.run_report({})
        self.assertEqual(self.section(report, 'books')['note'], 'no per-user accounts')
        self.assertEqual(self.section(report, 'music')['note'], 'not configured')
        self.assertEqual(report['problems'], [])

    def test_text_report_names_users_and_services(self):
        report = self.run_report({'music': FakeAccounts(names=['alice@example.com'])})
        text = format_report(report)
        self.assertIn('alice@example.com  [user]  books=shared, music=shared', text)
        self.assertIn('Music (music): 1 account(s)', text)
        self.assertIn('Cross-check: 0 problem(s)', text)


class JellyfinListTests(SimpleTestCase):
    def test_returns_display_names_only(self):
        client = FakeRows({'/Users': [{'Id': 'secret-id', 'Name': 'Alice', 'Policy': {}}]})
        adapter = JellyfinAdapter('http://jellyfin:8096', 'api-key', client=client)
        self.assertEqual(adapter.list_accounts(), ['Alice'])
        self.assertEqual(client.calls, [('GET', '/Users')])


class LycheeListTests(SimpleTestCase):
    def test_returns_usernames_from_user_management(self):
        client = FakeRows({'/api/v2/UserManagement': {'data': [{'id': 3, 'username': 'alice'}]}})
        adapter = LycheeAdapter('http://lychee', 'token', client_factory=client.factory)
        self.assertEqual(adapter.list_accounts(), ['alice'])


class PaperlessListTests(SimpleTestCase):
    def test_returns_usernames_from_the_users_api(self):
        client = FakeRows({'/api/users/?page_size=100': {'results': [{'id': 2, 'username': 'alice'}]}})
        adapter = PaperlessAdapter('http://paperless:8000', 'token', client_factory=client.factory)
        self.assertEqual(adapter.list_accounts(), ['alice'])


class RadicaleListTests(SimpleTestCase):
    def test_returns_login_names_and_never_the_password_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'users')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('Alice@Example.com:$2b$12$not-a-real-hash-for-tests\n')
            adapter = RadicaleAdapter(path)
            self.assertEqual(adapter.list_accounts(), ['alice@example.com'])


class NavidromeListTests(SimpleTestCase):
    def test_returns_usernames_after_admin_login(self):
        state = FakeNavidrome({1: ('Music', '/music')})
        state.users['101'] = {'userName': 'alice@example.com', 'password': 'pw', 'isAdmin': False}
        adapter = NavidromeAdapter('http://navidrome:4533', ADMIN, ADMIN_PASSWORD, client_factory=state.factory)
        self.assertEqual(adapter.list_accounts(), ['alice@example.com'])
        self.assertIn(('GET', '/api/user', None), state.calls)
