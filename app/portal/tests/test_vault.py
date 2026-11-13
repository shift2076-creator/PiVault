"""Dummy browser-generated fixture; no live credentials or native vault used."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from portal.invitations import issue
from portal.models import Notification, User
from portal.integrations.native_http import CreatedAccount, NativeError
from portal.runtime import PUBLIC_URLS, credential_plan
from portal.services import ADAPTERS, normalize_access, provision
from portal.integrations.vaultwarden import VaultwardenAdapter, VaultCleanupRequired, validate_bundle

PASSWORD = 'A-long-new-passphrase-482!'
EMAIL = 'alex@example.com'


def fixture():
    return json.loads((Path(__file__).parent / 'fixtures/vault_bundle.json').read_text())


class BundleTests(TestCase):
    def test_browser_bundle_accepts_same_password_and_expected_entries(self):
        value = fixture()
        self.assertIs(validate_bundle(value, PASSWORD, EMAIL, value['items']), value)

    def test_different_password_or_email_is_rejected(self):
        for password, email in [('Different-password-42!', EMAIL), (PASSWORD, 'other@example.com')]:
            with self.assertRaisesMessage(ValidationError, 'same password'):
                validate_bundle(fixture(), password, email, fixture()['items'])

    def test_missing_extra_or_malformed_cipher_is_rejected(self):
        for field in ('items', 'key', 'keys'):
            value = fixture(); del value[field]
            with self.assertRaises(ValidationError):
                validate_bundle(value, PASSWORD, EMAIL, fixture()['items'])
        value = fixture(); value['items']['pivault']['password'] = PASSWORD
        with self.assertRaises(ValidationError):
            validate_bundle(value, PASSWORD, EMAIL, fixture()['items'])
        with self.assertRaisesMessage(ValidationError, 'selected tools'):
            validate_bundle(fixture(), PASSWORD, EMAIL, {'pivault': {}})


class NativeVaultTests(TestCase):
    def setUp(self):
        self.user = User(email=EMAIL, display_name='Alex', role='user')
        self.client_api = Mock(base_url='http://vaultwarden:80')
        self.admin_api = self.client_api.admin.return_value
        self.auth = Mock()
        self.admin_api.request.side_effect = [NativeError('Password Manager', status=404), {'id': 'new-vault'}]
        self.client_api.request.side_effect = ['verification-token', {}]
        self.client_api.form.return_value = ({}, {'access_token': 'fixture-bearer'})
        self.auth.request.side_effect = [{'id': 'new-vault', 'email': EMAIL}, {'id': 'folder'},
                                        {'id': 'portal-item'}, {'id': 'vault-item'}]
        self.adapter = VaultwardenAdapter('http://vaultwarden:80', 'private-admin-token',
                                        'https://192.168.4.90', self.client_api)

    def call(self):
        with patch('portal.integrations.vaultwarden.VaultHTTP', return_value=self.auth):
            return self.adapter.provision(user=self.user, password=PASSWORD, mode='isolated', bundle=fixture())

    def test_registers_private_vault_and_only_encrypted_logins(self):
        receipt = self.call()
        self.assertEqual(receipt.native_id, 'new-vault')
        self.assertEqual(self.user.vault_items, {'pivault': 'portal-item', 'passwords': 'vault-item'})
        self.assertEqual(self.user.vault_user_id, 'new-vault')
        registration = self.client_api.request.call_args_list[1]
        self.assertEqual(registration.args[1], '/identity/accounts/register/finish')
        self.assertEqual(registration.args[2]['kdfIterations'], 600000)
        cipher = self.auth.request.call_args_list[-1].args[2]
        self.assertEqual(cipher['login']['uris'][0]['match'], 1)
        self.assertEqual(cipher['encryptedFor'], 'new-vault')
        serialized = repr(self.auth.request.call_args_list)
        self.assertNotIn(PASSWORD, serialized)
        self.assertNotIn('private-admin-token', serialized)

    def test_existing_same_email_vault_is_never_adopted_or_reset(self):
        self.admin_api.request.side_effect = None
        self.admin_api.request.return_value = {'id': 'existing-owner'}
        with self.assertRaisesMessage(ValidationError, 'was not changed'):
            self.call()
        self.assertEqual(self.admin_api.request.call_count, 1)
        self.client_api.request.assert_not_called()

    def test_failed_cipher_creation_removes_only_this_new_vault(self):
        self.admin_api.request.side_effect = [NativeError('Password Manager', status=404), {'id': 'new-vault'}, {}]
        self.auth.request.side_effect = [{'id': 'new-vault', 'email': EMAIL}, {'id': 'folder'},
                                        NativeError('Password Manager', status=500)]
        with self.assertRaises(NativeError), self.assertLogs('portal.setup', level='ERROR'):
            self.call()
        self.assertEqual(self.admin_api.request.call_args.args, ('POST', '/admin/users/new-vault/delete', {}))

    def test_cleanup_failure_has_reviewable_native_id_without_password(self):
        self.admin_api.request.side_effect = [NativeError('Password Manager', status=404), {'id': 'new-vault'},
                                             NativeError('Password Manager', status=500)]
        self.auth.request.side_effect = NativeError('Password Manager')
        with self.assertRaises(VaultCleanupRequired) as error, self.assertLogs('portal.setup', level='ERROR'):
            self.call()
        self.assertEqual(error.exception.cleanup_failures, [{'service': 'passwords', 'native_id': 'new-vault'}])
        self.assertNotIn(PASSWORD, str(error.exception))

    def test_ambiguous_invite_result_does_not_delete_unknown_existing_vault(self):
        self.admin_api.request.side_effect = [NativeError('Password Manager', status=404),
                                             NativeError('Password Manager', uncertain=True)]
        with self.assertRaises(VaultCleanupRequired):
            self.call()
        self.assertEqual(self.admin_api.request.call_count, 2)


class VaultEnrollmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', email='owner@example.com', role='admin',
            display_name='Owner', password='Owner-fixture-passphrase-42!')
        self.adapter = SimpleNamespace(modes=('isolated',), requires_browser_bundle=True,
            credential_username=lambda user: user.email, provision=Mock(side_effect=self.created))
        self.adapters = patch.dict(ADAPTERS, {'passwords': self.adapter}, clear=True)
        self.adapters.start(); self.addCleanup(self.adapters.stop)
        self.urls = patch.dict(PUBLIC_URLS, {'passwords': 'https://192.168.4.90'})
        self.urls.start(); self.addCleanup(self.urls.stop)
        # Books and Games default to Shared and would need a sealed copy; these tests cover the vault alone.
        self.invitation, self.code = issue('Alex', 'user', {'passwords': 'isolated', 'books': 'none', 'games': 'none'}, self.admin)

    def created(self, **values):
        values['user'].vault_user_id = 'new-vault'
        values['user'].vault_items = {'pivault': 'one', 'passwords': 'two'}
        return CreatedAccount('passwords', 'new-vault', Mock())

    def plan(self, **overrides):
        return self.client.post('/join/plan', {'code': self.code, 'email': EMAIL, **overrides}, secure=True)

    def join(self, payload=None):
        if payload is None:
            payload = {'token': self.plan().json()['token'], 'bundle': fixture()}
        return self.client.post('/join', {'code': self.code, 'email': EMAIL, 'password': PASSWORD,
            'password_confirm': PASSWORD, 'vault_bundle': json.dumps(payload)}, secure=True)

    def test_join_saves_native_ids_and_redirects_to_personal_completion_page(self):
        plan = self.plan().json()
        self.assertEqual(set(plan['items']), {'pivault', 'passwords'})
        response = self.join({'token': plan['token'], 'bundle': fixture()})
        self.assertRedirects(response, '/users/complete', fetch_redirect_response=False)
        user = User.objects.get(email=EMAIL)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(user.vault_items, {'pivault': 'one', 'passwords': 'two'})
        self.assertNotIn(PASSWORD, json.dumps(user.vault_items))
        self.assertNotIn('masterPasswordHash', json.dumps(user.vault_items))
        self.invitation.refresh_from_db(); self.assertIsNotNone(self.invitation.used_at)
        self.assertEqual(self.adapter.provision.call_args.kwargs['bundle'], fixture())

    def test_signed_manifest_cannot_be_reused_for_different_identity(self):
        token = self.plan(email='other@example.com').json()['token']
        with self.assertLogs('portal.setup', level='WARNING'):
            response = self.join({'token': token, 'bundle': fixture()})
        self.assertContains(response, 'Could not validate', status_code=400)
        self.adapter.provision.assert_not_called()
        self.assertFalse(User.objects.filter(email=EMAIL).exists())

    def test_missing_bundle_does_not_consume_invitation(self):
        with self.assertLogs('portal.setup', level='WARNING'):
            response = self.join({})
        self.assertContains(response, 'Could not validate', status_code=400)
        self.invitation.refresh_from_db(); self.assertIsNone(self.invitation.used_at)
        self.adapter.provision.assert_not_called()

    def test_failed_native_setup_keeps_invite_and_reports_cleanup_failure(self):
        self.adapter.provision.side_effect = VaultCleanupRequired('new-vault')
        with self.assertLogs('portal.setup', level='ERROR'):
            response = self.join()
        self.assertContains(response, 'admin must review', status_code=503)
        self.assertFalse(User.objects.filter(email=EMAIL).exists())
        self.invitation.refresh_from_db(); self.assertIsNone(self.invitation.used_at)
        self.assertTrue(Notification.objects.filter(source='users', level='error').exists())

    def test_completion_has_all_requested_clients_and_server_qr_without_secret(self):
        self.join()
        response = self.client.get('/users/complete', secure=True)
        for label in ('Android app', 'Firefox extension', 'iPhone / iPad', 'Chrome', 'Safari', 'Edge',
                      'Install Password Manager', 'Go to my Dashboard'):
            self.assertContains(response, label)
        self.assertNotContains(response, PASSWORD)

    def test_another_user_without_manager_cannot_open_completion(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/users/complete', secure=True).status_code, 403)

    def test_shared_manager_mode_is_rejected_server_side(self):
        with self.assertRaises(ValidationError):
            normalize_access({'passwords': 'shared'})

    def test_vault_runs_after_all_native_credentials_have_been_created(self):
        order = []
        native = SimpleNamespace(modes=('isolated',), provision=lambda **kwargs: order.append('videos'))
        self.adapter.provision.side_effect = lambda **kwargs: order.append('passwords')
        user = User(service_access={'passwords': 'isolated', 'videos': 'isolated'})
        with patch.dict(ADAPTERS, {'videos': native}):
            provision(user, PASSWORD, fixture())
        self.assertEqual(order, ['videos', 'passwords'])
