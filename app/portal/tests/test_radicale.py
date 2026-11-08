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

    def test_provision_adds_one_bcrypt_login_and_keeps_existing_lines(self):
        receipt = self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertEqual(receipt.service, 'radicale')
        self.assertEqual(receipt.native_id, 'sam@example.com')
        lines = self.lines()
        self.assertEqual(lines[0], f'snx:{self.existing}')
        name, _, digest = lines[1].partition(':')
        self.assertEqual(name, 'sam@example.com')
        self.assertTrue(digest.startswith('$2b$12$'))
        self.assertTrue(bcrypt.checkpw(b'Sam-passphrase-42', digest.encode('ascii')))

    def test_rollback_removes_only_the_new_login(self):
        before = self.lines()
        receipt = self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        receipt.rollback()
        self.assertEqual(self.lines(), before)

    def test_unverified_login_is_removed_and_the_enrollment_fails(self):
        before = self.lines()
        with patch.object(self.adapter, '_verifies', return_value=False):
            with self.assertRaisesMessage(ValidationError, 'could not be verified'):
                self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertEqual(self.lines(), before)

    def test_duplicate_login_is_rejected_and_the_file_is_unchanged(self):
        with open(self.path, 'a', encoding='utf-8') as handle:
            handle.write(f'sam@example.com:{self.existing}\n')
        before = self.lines()
        with self.assertRaisesMessage(ValidationError, 'already uses that email'):
            self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertEqual(self.lines(), before)

    def test_only_per_user_mode_is_supported(self):
        with self.assertRaisesMessage(ValidationError, 'does not support that access mode'):
            self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='shared')

    def test_passwords_longer_than_bcrypt_allows_are_refused_before_writing(self):
        before = self.lines()
        with self.assertRaisesMessage(ValidationError, '72 bytes or fewer'):
            self.adapter.provision(user=self.user, password='x' * 73, mode='isolated')
        self.assertEqual(self.lines(), before)

    def test_missing_login_file_is_reported_not_created(self):
        os.remove(self.path)
        with self.assertRaisesMessage(ValidationError, 'login file is missing'):
            self.adapter.provision(user=self.user, password='Sam-passphrase-42', mode='isolated')
        self.assertFalse(os.path.exists(self.path))

    def test_radicale_row_offers_per_user_only_once_configured(self):
        from portal.services import ADAPTERS
        admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')
        self.client.force_login(admin)
        markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertNotIn('value="isolated"', row(markup, 'radicale'))
        with patch.dict(ADAPTERS, {'radicale': self.adapter}):
            markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertIn('value="isolated"', row(markup, 'radicale'))
        self.assertIn('value="none"', row(markup, 'radicale'))
