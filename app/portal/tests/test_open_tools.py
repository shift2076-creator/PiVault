from importlib import import_module
from django.apps import apps
from types import SimpleNamespace
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from portal.invitations import issue
from portal.models import Invitation, User
from portal.services import provision

migration = import_module('portal.migrations.0007_open_tools_shared')


class OpenToolsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')

    def test_always_shared_tools_are_stored_shared_whatever_is_posted(self):
        issue('Alex', 'user', {'books': 'none', 'wiki': 'none', 'documents': 'none'}, self.admin)
        services = Invitation.objects.get().services
        self.assertEqual(services['books'], 'shared')
        self.assertEqual(services['wiki'], 'shared')
        self.assertEqual(services['documents'], 'none')

    def test_open_tools_reject_modes_without_an_account_adapter(self):
        with self.assertRaisesMessage(ValidationError, 'Documents: isolated is not available.'):
            issue('Alex', 'user', {'documents': 'isolated'}, self.admin)
        self.assertFalse(Invitation.objects.exists())

    def test_admin_page_shows_always_shared_tools_as_text_and_others_as_dropdowns(self):
        from portal.services import ADAPTERS
        self.client.force_login(self.admin)
        with patch.dict(ADAPTERS, {'videos': SimpleNamespace(modes=('shared',))}):
            markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertEqual(markup.count('fixed-access">Shared</span>'), 4)
        for key in ('books', 'games', 'videos', 'wiki'):
            self.assertNotIn(f'name="service_{key}"', markup)
        self.assertIn('name="service_documents"', markup)
        self.assertRegex(markup, r'<option value="shared" selected>Shared</option>')
        self.assertIn('name="service_passwords"', markup)

    def test_admin_dropdown_for_documents_offers_only_shared_and_no_access(self):
        self.client.force_login(self.admin)
        markup = self.client.get('/users/add', secure=True).content.decode()
        block = markup.split('name="service_documents"', 1)[1].split('</select>', 1)[0]
        self.assertIn('value="shared"', block)
        self.assertIn('value="none"', block)
        self.assertNotIn('value="isolated"', block)

    def test_every_service_has_a_launcher_category_and_app_name(self):
        from portal.services import SERVICE_DETAILS, SERVICES
        self.assertEqual({key for key, _ in SERVICES}, set(SERVICE_DETAILS))

    def test_per_user_is_offered_only_where_an_adapter_supports_it(self):
        from portal.services import ADAPTERS
        self.client.force_login(self.admin)
        def select(markup, key):
            return markup.split(f'name="service_{key}"', 1)[1].split('</select>', 1)[0]
        with patch.dict(ADAPTERS, {}, clear=True):
            markup = self.client.get('/users/add', secure=True).content.decode()
            self.assertNotIn('value="isolated"', select(markup, 'photos'))
        with patch.dict(ADAPTERS, {'photos': SimpleNamespace(modes=('isolated',))}):
            markup = self.client.get('/users/add', secure=True).content.decode()
            self.assertIn('>Per user</option>', select(markup, 'photos'))

    def test_music_offers_per_user_only_when_private_folders_are_configured(self):
        from portal.services import ADAPTERS
        self.client.force_login(self.admin)
        def select(markup, key):
            return markup.split(f'name="service_{key}"', 1)[1].split('</select>', 1)[0]
        with patch.dict(ADAPTERS, {'music': SimpleNamespace(modes=('shared',))}):
            markup = self.client.get('/users/add', secure=True).content.decode()
            self.assertNotIn('value="isolated"', select(markup, 'music'))
        with patch.dict(ADAPTERS, {'music': SimpleNamespace(modes=('shared', 'isolated'))}):
            markup = self.client.get('/users/add', secure=True).content.decode()
            self.assertIn('>Per user</option>', select(markup, 'music'))

    def test_password_manager_is_fixed_per_user(self):
        from portal.services import ADAPTERS
        self.client.force_login(self.admin)
        with patch.dict(ADAPTERS, {'passwords': SimpleNamespace(modes=('isolated',))}):
            markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertNotIn('name="service_passwords"', markup)
        self.assertIn('Per user', markup)

    def test_admin_page_groups_rows_by_launcher_category_with_app_names(self):
        self.client.force_login(self.admin)
        markup = self.client.get('/users/add', secure=True).content.decode()
        headings = [h for h in ('Entertainment', 'Knowledge', 'Home Tools', 'Tools') if f'<h3 class="category">{h}</h3>' in markup]
        self.assertEqual(headings, ['Entertainment', 'Knowledge', 'Home Tools', 'Tools'])
        self.assertLess(markup.index('>Entertainment<'), markup.index('>Knowledge<'))
        for name in ('Calibre-Web', 'RetroAssembly', 'Navidrome', 'Jellyfin', 'Paperless-ngx', 'Kiwix', 'Lychee', 'Vaultwarden'):
            self.assertIn(f'<span class="app">{name}</span>', markup)

    def test_provisioning_open_tools_creates_no_accounts(self):
        user = User(username='sam', email='sam@example.com', display_name='Sam', role='user',
                    service_access={'books': 'shared', 'wiki': 'shared', 'games': 'none', 'passwords': 'none'})
        self.assertEqual(provision(user, 'pw'), [])

    def test_gate_follows_the_open_tool_choice(self):
        user = User.objects.create_user(username='sam', email='sam@example.com', password='x',
            display_name='Sam', role='user', service_access={'books': 'shared', 'documents': 'none'})
        self.client.force_login(user)
        self.assertEqual(self.client.get('/internal/gate?service=books', secure=True).status_code, 204)
        self.assertEqual(self.client.get('/internal/gate?service=documents', secure=True).status_code, 403)
        self.assertEqual(self.client.get('/internal/gate?service=games', secure=True).status_code, 403)

    def test_migration_shares_open_tools_with_existing_users(self):
        user = User.objects.create_user(username='pat', email='pat@example.com', password='x',
            display_name='Pat', role='user', service_access={'books': 'none', 'videos': 'none'})
        migration.share_open_tools(apps, None)
        user.refresh_from_db()
        self.assertEqual(user.service_access['books'], 'shared')
        self.assertEqual(user.service_access['documents'], 'shared')
        self.assertEqual(user.service_access['videos'], 'none')
