"""One shared login per shared service, copied from the admin's vault into each new vault.

The server never receives the key that opens the copy; these tests pin the server's half of that contract.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
import io

from django.core.exceptions import ValidationError
from django.test import TestCase

from portal.invitations import attach_shared, issue
from portal.models import Invitation, User
from portal.runtime import PUBLIC_URLS, credential_plan
from portal.services import ADAPTERS, SERVICES, SHARED_LOGIN

# A well-formed sealed value: "2." + iv(16) | cipher(16) | mac(32), all base64.
SEALED = '2.' + 'A' * 22 + '==|' + 'B' * 22 + '==|' + 'C' * 43 + '='


def stub(**attributes):
    return SimpleNamespace(**attributes)


class SharedPlanTests(TestCase):
    def setUp(self):
        self.user = User(email='alex@example.com', display_name='Alex', role='user', service_access={
            'passwords': 'isolated', 'books': 'shared', 'games': 'shared', 'music': 'shared', 'videos': 'shared',
            'photos': 'shared', 'radicale': 'shared', 'documents': 'isolated'})
        self.adapters = patch.dict(ADAPTERS, {
            'passwords': stub(credential_username=lambda user: user.email),
            'documents': stub(credential_username=lambda user: user.email),
            'music': stub(credential_username=lambda user: user.email),
            'videos': stub(credential_username=lambda user: user.display_name),
        })
        self.adapters.start()
        self.addCleanup(self.adapters.stop)
        self.urls = patch.dict(PUBLIC_URLS, {'passwords': 'https://192.168.4.90', 'documents': 'https://192.168.4.90:8444'})
        self.urls.start()
        self.addCleanup(self.urls.stop)

    def test_each_shared_login_is_a_fixed_name_with_no_per_person_fields(self):
        plan = credential_plan(self.user)
        for key in SHARED_LOGIN:
            self.assertEqual(plan[key], {'name': 'PiVault ' + dict(SERVICES)[key], 'shared': True}, key)

    def test_shared_logins_do_not_need_a_client_address(self):
        # Books and Games have no adapter, so the admin's sealed copy is their only source of an address.
        addresses = {'pivault': 'https://192.168.4.90/users/sign-in', 'passwords': 'https://192.168.4.90',
                     'documents': 'https://192.168.4.90:8444'}
        with patch.dict(PUBLIC_URLS, addresses, clear=True):
            plan = credential_plan(self.user)
        self.assertTrue(plan['books']['shared'])

    def test_per_person_services_keep_their_own_account_fields(self):
        plan = credential_plan(self.user)
        self.assertEqual(plan['documents']['username'], 'alex@example.com')
        self.assertNotIn('shared', plan['documents'])

    def test_a_service_set_to_none_is_left_out(self):
        self.user.service_access['music'] = 'none'
        self.assertNotIn('music', credential_plan(self.user))


class AttachSharedTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='owner', email='owner@example.com', role='admin',
            display_name='Owner', password='Owner-password-123!')
        self.adapters = patch.dict(ADAPTERS, {'passwords': stub(modes=('isolated',)),
            'music': stub(modes=('shared',))}, clear=False)
        self.adapters.start()
        self.addCleanup(self.adapters.stop)
        self.invitation, self.code = issue('Alex', 'user', {'passwords': 'isolated', 'music': 'shared',
            'books': 'none', 'games': 'none'}, self.admin)

    def test_stores_the_sealed_copy_on_the_open_invitation(self):
        attach_shared(self.code, self.admin, SEALED)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.shared_sealed, SEALED)

    def test_only_the_admin_who_issued_it_can_attach(self):
        other = User.objects.create_user(username='second', email='second@example.com', role='admin',
            display_name='Second', password='Second-password-123!')
        with self.assertRaisesMessage(ValidationError, 'no longer open'):
            attach_shared(self.code, other, SEALED)

    def test_used_or_expired_invitations_refuse_a_copy(self):
        Invitation.objects.filter(pk=self.invitation.pk).update(used_at=self.invitation.expires_at)
        with self.assertRaisesMessage(ValidationError, 'no longer open'):
            attach_shared(self.code, self.admin, SEALED)

    def test_invitation_without_shared_logins_is_refused(self):
        _, code = issue('Sam', 'user', {'passwords': 'isolated', 'music': 'none', 'books': 'none',
            'games': 'none', 'videos': 'none'}, self.admin)
        with self.assertRaisesMessage(ValidationError, 'no shared logins'):
            attach_shared(code, self.admin, SEALED)

    def test_malformed_sealed_value_is_refused(self):
        for bad in ('plain text', '2.AAAA|BBBB|CCCC', SEALED.replace('|', '', 1), 'x' * 20000):
            with self.subTest(bad=bad[:20]), self.assertRaises(ValidationError):
                attach_shared(self.code, self.admin, bad)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.shared_sealed, '')

    def test_attach_endpoint_is_admin_only(self):
        self.client.force_login(User.objects.create_user(username='member', email='member@example.com',
            role='user', display_name='Member', password='Member-password-123!'))
        response = self.client.post('/users/add/shared', {'code': self.code, 'sealed': SEALED}, secure=True)
        self.assertEqual(response.status_code, 403)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.shared_sealed, '')

    def test_attach_endpoint_reports_errors_as_json(self):
        self.client.force_login(self.admin)
        response = self.client.post('/users/add/shared', {'code': self.code, 'sealed': 'not sealed'}, secure=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())


class PlanRequiresSharedCopyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='owner', email='owner@example.com', role='admin',
            display_name='Owner', password='Owner-password-123!')
        self.vault = stub(client=stub(base_url='http://vaultwarden:80'),
            requires_browser_bundle=True, modes=('isolated',), credential_username=lambda user: user.email)
        self.adapters = patch.dict(ADAPTERS, {'passwords': self.vault, 'music': stub(modes=('shared',))})
        self.adapters.start()
        self.addCleanup(self.adapters.stop)
        self.urls = patch.dict(PUBLIC_URLS, {'passwords': 'https://192.168.4.90'})
        self.urls.start()
        self.addCleanup(self.urls.stop)
        self.invitation, self.code = issue('Alex', 'user', {'passwords': 'isolated', 'music': 'shared',
            'books': 'none', 'games': 'none'}, self.admin)

    def plan(self):
        return self.client.post('/join/plan', {'code': self.code, 'email': 'alex@example.com'}, secure=True)

    def test_plan_is_refused_until_the_admin_copies_the_shared_logins(self):
        response = self.plan()
        self.assertEqual(response.status_code, 400)
        self.assertIn('have not been copied', response.json()['error'])

    def test_plan_carries_the_sealed_copy_and_marks_shared_entries(self):
        attach_shared(self.code, self.admin, SEALED)
        body = self.plan().json()
        self.assertEqual(body['shared'], SEALED)
        self.assertEqual(body['items']['music'], {'name': 'PiVault Music', 'shared': True})
        self.assertIn('token', body)

    def test_plan_without_shared_logins_has_no_copy_attached(self):
        self.invitation.services = {'passwords': 'isolated', 'music': 'none', 'books': 'none', 'games': 'none'}
        self.invitation.save()
        body = self.plan().json()
        self.assertNotIn('music', body['items'])
        self.assertEqual(body['shared'], '')


class VaultRelayTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='owner', email='owner@example.com', role='admin',
            display_name='Owner', password='Owner-password-123!')
        self.member = User.objects.create_user(username='member', email='member@example.com', role='user',
            display_name='Member', password='Member-password-123!')
        self.adapters = patch.dict(ADAPTERS, {'passwords': stub(client=stub(base_url='http://vaultwarden:80'))})
        self.adapters.start()
        self.addCleanup(self.adapters.stop)
        self.opened = patch('portal.vault_views.urlopen')
        self.urlopen = self.opened.start()
        self.addCleanup(self.opened.stop)
        self.client.force_login(self.admin)

    def reply(self, body):
        return io.BytesIO(json.dumps(body).encode())

    def test_only_admins_reach_the_relay(self):
        self.client.force_login(self.member)
        response = self.client.post('/users/vault-proxy/prelogin', json.dumps({'email': 'a@b.co'}),
            content_type='application/json', secure=True)
        self.assertEqual(response.status_code, 403)
        self.urlopen.assert_not_called()

    def test_unknown_paths_are_not_relayed(self):
        response = self.client.post('/users/vault-proxy/admin', {}, secure=True)
        self.assertEqual(response.status_code, 404)
        self.urlopen.assert_not_called()

    def test_wrong_method_is_refused(self):
        response = self.client.get('/users/vault-proxy/token', secure=True)
        self.assertEqual(response.status_code, 405)

    def test_token_relay_accepts_only_the_password_grant_and_named_fields(self):
        for values in ({'grant_type': 'client_credentials'}, {'grant_type': 'password', 'admin_token': 'x'}):
            with self.subTest(values=values):
                response = self.client.post('/users/vault-proxy/token', values, secure=True)
                self.assertEqual(response.status_code, 400)
        self.urlopen.assert_not_called()

    def test_token_relay_forwards_to_the_identity_path_only(self):
        self.urlopen.return_value = self.reply({'access_token': 'bearer', 'Key': '2.x|y|z'})
        response = self.client.post('/users/vault-proxy/token', {
            'grant_type': 'password', 'username': 'owner@example.com', 'password': 'hash', 'scope': 'api offline_access',
            'client_id': 'web', 'deviceType': '9', 'deviceIdentifier': 'device', 'deviceName': 'PiVault share',
        }, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['access_token'], 'bearer')
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'http://vaultwarden:80/identity/connect/token')
        self.assertIn(b'grant_type=password', request.data)

    def test_prelogin_relay_sends_only_the_email(self):
        self.urlopen.return_value = self.reply({'Kdf': 0, 'KdfIterations': 600000})
        response = self.client.post('/users/vault-proxy/prelogin', json.dumps({'email': 'owner@example.com'}),
            content_type='application/json', secure=True)
        self.assertEqual(response.json()['KdfIterations'], 600000)
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'http://vaultwarden:80/identity/accounts/prelogin')
        self.assertEqual(json.loads(request.data), {'email': 'owner@example.com'})
        response = self.client.post('/users/vault-proxy/prelogin', json.dumps({'email': 'a@b.co', 'x': 1}),
            content_type='application/json', secure=True)
        self.assertEqual(response.status_code, 400)

    def test_sync_needs_a_bearer_token_and_fixed_path(self):
        response = self.client.get('/users/vault-proxy/sync', secure=True)
        self.assertEqual(response.status_code, 401)
        self.urlopen.return_value = self.reply({'Ciphers': []})
        response = self.client.get('/users/vault-proxy/sync', secure=True, HTTP_AUTHORIZATION='Bearer token-1')
        self.assertEqual(response.json(), {'Ciphers': []})
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'http://vaultwarden:80/api/sync?excludeDomains=true')
        self.assertEqual(request.get_header('Authorization'), 'Bearer token-1')

    def test_vault_rejections_are_passed_back_without_detail(self):
        error = HTTPError('http://vaultwarden:80/identity/connect/token', 400, 'Bad Request', {},
            io.BytesIO(b'{"error_description":"invalid_username_or_password","secret":"DO_NOT_REPORT"}'))
        self.urlopen.side_effect = error
        response = self.client.post('/users/vault-proxy/token', {'grant_type': 'password', 'password': 'DO_NOT_REPORT'},
            secure=True)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'The Password Manager rejected the request.', 'status': 400})
        self.assertNotIn('DO_NOT_REPORT', response.content.decode())


class AddUserSharedKeyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='owner', email='owner@example.com', role='admin',
            display_name='Owner', password='Owner-password-123!')
        self.adapters = patch.dict(ADAPTERS, {'passwords': stub(modes=('isolated',)),
            'music': stub(modes=('shared',))})
        self.adapters.start()
        self.addCleanup(self.adapters.stop)
        self.client.force_login(self.admin)

    def test_shared_invitation_page_gets_a_key_that_is_never_stored(self):
        response = self.client.post('/users/add', {'name': 'Alex', 'role': 'user', 'service_passwords': 'isolated',
            'service_music': 'shared', 'service_books': 'none', 'service_games': 'none'}, secure=True)
        self.assertContains(response, 'id="shared-copy"')
        key = response.context['shared_key']
        self.assertGreaterEqual(len(key), 40)
        self.assertNotIn(key, json.dumps(list(Invitation.objects.values()), default=str))

    def test_invitation_without_shared_logins_shows_no_copy_panel(self):
        response = self.client.post('/users/add', {'name': 'Sam', 'role': 'user', 'service_passwords': 'isolated',
            'service_music': 'none', 'service_books': 'none', 'service_games': 'none'}, secure=True)
        self.assertNotContains(response, 'id="shared-copy"')
        self.assertEqual(response.context['shared_key'], '')
