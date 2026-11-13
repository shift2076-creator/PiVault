from django.test import TestCase

from portal.models import User


class AddUserWizardTests(TestCase):
    """The admin Add user page is a six-step wizard. The server flow is unchanged; these tests pin the markup it depends on."""

    def setUp(self):
        self.admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')
        self.client.force_login(self.admin)

    def test_admin_page_has_the_step_bar_and_panes_for_every_step(self):
        markup = self.client.get('/users/add', secure=True).content.decode()
        self.assertIn('class="wizard-steps"', markup)
        self.assertIn('class="wizard-nav"', markup)
        for step in ('1', '2', '3', '4', '5', '6'):
            self.assertIn(f'data-wizard-step="{step}"', markup)
        for pane in ('1', '2', '3'):
            self.assertIn(f'data-pane="{pane}"', markup)

    def test_invitation_page_has_the_invite_and_phone_panes(self):
        response = self.client.post('/users/add', {'name': 'Sam Rivera', 'role': 'user'}, secure=True)
        self.assertEqual(response.status_code, 200)
        markup = response.content.decode()
        self.assertIn('data-pane="5"', markup)
        self.assertIn('data-pane="6"', markup)
        self.assertIn('class="invitation"', markup)

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
