from unittest.mock import patch

from django.test import TestCase

from portal.client_packages import maintain
from portal.notifications import notify


class ClientFailureResolutionTests(TestCase):
    def test_clean_check_resolves_earlier_failure_notices(self):
        item = notify('clients', 'check-davx5-2026-11-05', 'DAVx5 app release check failed',
                      'The official release could not be checked.', level='warning')
        with patch('portal.client_packages.discover', return_value={}), \
                patch('portal.client_packages.record_release'):
            self.assertEqual(maintain(check_only=True), [])
        item.refresh_from_db()
        self.assertEqual(item.status, 'resolved')
