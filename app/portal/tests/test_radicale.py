import os
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import bcrypt
from django.core.exceptions import ValidationError
from django.test import TestCase

from portal.integrations.radicale import RadicaleAdapter
from portal.models import User


def row(markup, key):
    return markup.split(f'data-service="{key}"', 1)[1].split('</div>', 1)[0]


class RadicaleAdapterTests(TestCase):
    """Calendar and contacts are shared or none: one login for everyone, never a per-person entry."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.path = os.path.join(self.folder, 'users')
        self.existing = bcrypt.hashpw(b'Existing-passphrase-1', bcrypt.gensalt(4)).decode()
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(f'snx:{self.existing}\n')
        self.adapter = RadicaleAdapter(self.path)
        self.user = SimpleNamespace(email='Sam@Example.com', role='user')

    def lines(self):
        with open(self.path, encoding='utf-8') as handle:
            return handle.read().splitlines()

    def test_list_accounts_returns_login_names_without_hashes(self):
        self.assertEqual(self.adapter.list_accounts(), ['snx'])

    def test_shared_provision_writes_nothing(self):
        self.assertIsNone(self.adapter.provision(user=self.user, password='Shared-passphrase-2', mode='shared'))
        self.assertEqual(self.lines(), [f'snx:{self.existing}'])

    def test_per_user_mode_is_refused(self):
        with self.assertRaisesMessage(ValidationError, 'shared or none'):
            self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertEqual(self.lines(), [f'snx:{self.existing}'])

    def test_missing_login_file_is_reported_not_created(self):
        os.remove(self.path)
        with self.assertRaisesMessage(ValidationError, 'missing'):
            self.adapter.list_accounts()
        self.assertFalse(os.path.exists(self.path))

    def test_radicale_row_offers_shared_or_none_once_configured(self):
        from portal.services import ADAPTERS
        admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')
        self.client.force_login(admin)
        markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertNotIn('value="shared"', row(markup, 'radicale'))
        with patch.dict(ADAPTERS, {'radicale': self.adapter}):
            markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertIn('value="shared"', row(markup, 'radicale'))
        self.assertIn('value="none"', row(markup, 'radicale'))
        self.assertNotIn('value="isolated"', row(markup, 'radicale'))
