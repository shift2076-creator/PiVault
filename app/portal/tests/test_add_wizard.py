from unittest.mock import patch

from django.test import TestCase

from portal.models import User
from portal.services import ADAPTERS


class _VaultStub:
    modes = ('isolated',)
    requires_browser_bundle = True


class AddUserWizardTests(TestCase):
    """The admin Add user page is a five-step wizard: Person, Access (creates the invitation), Shared logins, Invite, Phone. These tests pin the markup it depends on."""

    def setUp(self):
        self.admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')
        self.client.force_login(self.admin)

    def test_admin_page_has_the_step_bar_and_panes_for_every_step(self):
        markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertIn('class="wizard-steps"', markup)
        self.assertIn('class="wizard-nav"', markup)
        for step in ('1', '2', '3', '4', '5'):
            self.assertIn(f'data-wizard-step="{step}"', markup)
        self.assertNotIn('data-wizard-step="6"', markup)
        self.assertNotIn('Create accounts', markup)
        for pane in ('1', '2'):
            self.assertIn(f'data-pane="{pane}"', markup)
        # The review and the Create invitation button belong to step 2 (Access), not to a separate step.
        self.assertIn('<div class="review" data-pane="2"', markup)
        self.assertIn('<button class="submit" type="submit" data-pane="2"', markup)

    def test_invitation_page_has_the_invite_and_phone_panes(self):
        response = self.client.post('/users/add', {'name': 'Sam Rivera', 'role': 'user'}, secure=True)
        self.assertEqual(response.status_code, 200)
        markup = response.content.decode()
        self.assertIn('data-pane="4"', markup)
        self.assertIn('data-pane="5"', markup)
        self.assertIn('class="invitation"', markup)

    def test_shared_login_step_uses_the_lowercase_admin_email(self):
        # The vault key is derived from the email; Vaultwarden expects it lowercased, so a capital letter
        # would make a correct master password fail.
        self.admin.email = 'Owner@Example.com'
        self.admin.save(update_fields=['email'])
        with patch.dict(ADAPTERS, {'passwords': _VaultStub()}):
            response = self.client.post('/users/add', {'name': 'Sam Rivera', 'role': 'user',
                'service_books': 'shared', 'service_passwords': 'isolated'}, secure=True)
        markup = response.content.decode()
        self.assertIn('data-email="owner@example.com"', markup)
        self.assertNotIn('Owner@Example.com', markup)

    def test_admin_page_loads_the_wizard_script(self):
        markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertIn('src="/users/add-wizard.js"', markup)

    def test_wizard_script_is_served_as_javascript(self):
        response = self.client.get('/users/add-wizard.js', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript; charset=utf-8')
        self.assertIn(b"document.body.dataset.mode !== 'admin'", response.content)

    def test_sign_in_page_does_not_load_the_wizard(self):
        self.client.logout()
        markup = self.client.get('/users/sign-in', secure=True).content.decode()
        self.assertNotIn('add-wizard.js', markup)
        self.assertNotIn('class="wizard-steps"', markup)
