from importlib import import_module
from django.apps import apps
from django.test import TestCase
from portal.invitations import issue
from portal.models import Invitation, User
from portal.services import provision

migration = import_module('portal.migrations.0007_open_tools_shared')


class OpenToolsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')

    def test_open_tools_are_always_shared_on_new_invitations(self):
        issue('Alex', 'user', {'books': 'none', 'wiki': 'none'}, self.admin)
        services = Invitation.objects.get().services
        self.assertEqual(services['books'], 'shared')
        self.assertEqual(services['wiki'], 'shared')

    def test_admin_page_shows_open_tools_as_text_not_a_dropdown(self):
        self.client.force_login(self.admin)
        markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertIn('Shared with all users', markup)
        self.assertNotIn('name="service_books"', markup)
        self.assertNotIn('name="service_wiki"', markup)
        self.assertIn('name="service_passwords"', markup)

    def test_provisioning_open_tools_creates_no_accounts(self):
        user = User(username='sam', email='sam@example.com', display_name='Sam', role='user',
                    service_access={'books': 'shared', 'wiki': 'shared', 'games': 'none', 'passwords': 'none'})
        self.assertEqual(provision(user, 'pw'), [])

    def test_gate_lets_signed_in_users_use_open_tools(self):
        user = User.objects.create_user(username='sam', email='sam@example.com', password='x',
            display_name='Sam', role='user', service_access={'books': 'shared', 'wiki': 'shared'})
        self.client.force_login(user)
        self.assertEqual(self.client.get('/internal/gate?service=books', secure=True).status_code, 204)
        self.assertEqual(self.client.get('/internal/gate?service=games', secure=True).status_code, 403)

    def test_migration_shares_open_tools_with_existing_users(self):
        user = User.objects.create_user(username='pat', email='pat@example.com', password='x',
            display_name='Pat', role='user', service_access={'books': 'none', 'videos': 'none'})
        migration.share_open_tools(apps, None)
        user.refresh_from_db()
        self.assertEqual(user.service_access['books'], 'shared')
        self.assertEqual(user.service_access['documents'], 'shared')
        self.assertEqual(user.service_access['videos'], 'none')
