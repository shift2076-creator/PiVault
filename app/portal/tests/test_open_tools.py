from importlib import import_module
from django.apps import apps
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

    def test_open_tools_keep_the_admin_choice_on_new_invitations(self):
        issue('Alex', 'user', {'books': 'none', 'wiki': 'shared'}, self.admin)
        services = Invitation.objects.get().services
        self.assertEqual(services['books'], 'none')
        self.assertEqual(services['wiki'], 'shared')

    def test_open_tools_reject_modes_without_an_account_adapter(self):
        with self.assertRaisesMessage(ValidationError, 'Books: isolated is not available.'):
            issue('Alex', 'user', {'books': 'isolated'}, self.admin)
        self.assertFalse(Invitation.objects.exists())

    def test_admin_page_shows_open_tools_as_dropdowns_defaulting_to_shared(self):
        self.client.force_login(self.admin)
        markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertIn('name="service_books"', markup)
        self.assertIn('name="service_wiki"', markup)
        self.assertNotIn('Shared with all users', markup)
        self.assertRegex(markup, r'<option value="shared" selected>Shared</option>')
        self.assertIn('name="service_passwords"', markup)

    def test_admin_page_groups_rows_by_launcher_category_with_app_names(self):
        self.client.force_login(self.admin)
        markup = self.client.get('/users/add', secure=True).content.decode()
        headings = [h for h in ('Entertainment', 'Knowledge', 'Tools') if f'<h3 class="category">{h}</h3>' in markup]
        self.assertEqual(headings, ['Entertainment', 'Knowledge', 'Tools'])
        self.assertLess(markup.index('>Entertainment<'), markup.index('>Knowledge<'))
        for name in ('Calibre-Web', 'Arcade', 'Navidrome', 'Jellyfin', 'Paperless', 'Kiwix', 'Vaultwarden'):
            self.assertIn(f'<span class="app">{name}</span>', markup)

    def test_admin_dropdown_for_open_tools_offers_only_shared_and_no_access(self):
        self.client.force_login(self.admin)
        markup = self.client.get('/users/add', secure=True).content.decode()
        block = markup.split('name="service_books"', 1)[1].split('</select>', 1)[0]
        self.assertIn('value="shared"', block)
        self.assertIn('value="none"', block)
        self.assertNotIn('value="isolated"', block)

    def test_provisioning_open_tools_creates_no_accounts(self):
        user = User(username='sam', email='sam@example.com', display_name='Sam', role='user',
                    service_access={'books': 'shared', 'wiki': 'shared', 'games': 'none', 'passwords': 'none'})
        self.assertEqual(provision(user, 'pw'), [])

    def test_gate_follows_the_open_tool_choice(self):
        user = User.objects.create_user(username='sam', email='sam@example.com', password='x',
            display_name='Sam', role='user', service_access={'books': 'shared', 'wiki': 'none'})
        self.client.force_login(user)
        self.assertEqual(self.client.get('/internal/gate?service=books', secure=True).status_code, 204)
        self.assertEqual(self.client.get('/internal/gate?service=wiki', secure=True).status_code, 403)
        self.assertEqual(self.client.get('/internal/gate?service=games', secure=True).status_code, 403)

    def test_migration_shares_open_tools_with_existing_users(self):
        user = User.objects.create_user(username='pat', email='pat@example.com', password='x',
            display_name='Pat', role='user', service_access={'books': 'none', 'videos': 'none'})
        migration.share_open_tools(apps, None)
        user.refresh_from_db()
        self.assertEqual(user.service_access['books'], 'shared')
        self.assertEqual(user.service_access['documents'], 'shared')
        self.assertEqual(user.service_access['videos'], 'none')
