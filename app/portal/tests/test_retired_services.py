from datetime import timedelta
from importlib import import_module
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from portal.invitations import issue
from portal.models import Invitation, User
from portal.services import SERVICES

retire = import_module('portal.migrations.0006_retire_inventory_pantry')


class RetiredServicesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')

    def test_inventory_and_pantry_are_no_longer_services(self):
        keys = {key for key, _ in SERVICES}
        self.assertNotIn('inventory', keys)
        self.assertNotIn('pantry', keys)

    def test_new_invitations_cannot_name_retired_services(self):
        with self.assertRaisesMessage(ValidationError, 'Unknown service'):
            issue('Alex', 'user', {'inventory': 'shared'}, self.admin)
        self.assertFalse(Invitation.objects.exists())

    def test_migration_drops_retired_access_vault_entries_and_invitation_grants(self):
        user = User.objects.create_user(username='sam', email='sam@example.com', password='x',
            display_name='Sam', role='user', service_access={'inventory': 'shared', 'books': 'shared', 'pantry': 'none'},
            vault_items={'inventory': 'cipher-1', 'passwords': 'cipher-2'})
        invitation = Invitation.objects.create(digest='a' * 64, name='Pat', role='user',
            services={'pantry': 'shared', 'wiki': 'shared'}, created_by=self.admin,
            expires_at=timezone.now() + timedelta(minutes=30))
        retire.remove_retired(apps, None)
        user.refresh_from_db()
        invitation.refresh_from_db()
        self.assertEqual(user.service_access, {'books': 'shared'})
        self.assertEqual(user.vault_items, {'passwords': 'cipher-2'})
        self.assertEqual(invitation.services, {'wiki': 'shared'})
