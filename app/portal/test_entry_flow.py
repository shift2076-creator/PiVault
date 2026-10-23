import re
from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from portal.models import User

class EntryFlowTests(TestCase):
 def test_admin_sign_in_goes_to_dashboard(self):
  User.objects.create_user(username='owner',email='owner@example.com',role='admin',display_name='Owner',password='Local-only-passphrase-849!')
  result=self.client.post('/users/sign-in',{'email':'owner@example.com','password':'Local-only-passphrase-849!'},secure=True)
  self.assertRedirects(result,'/',fetch_redirect_response=False)
  self.assertEqual(self.client.get('/users/session',secure=True).json(),{'authenticated':True,'admin':True})
  self.assertEqual(self.client.get('/users/add',secure=True).status_code,200)
 def test_first_admin_join_goes_to_dashboard(self):
  output=StringIO();call_command('first_admin','Owner',stdout=output)
  code=re.search(r'Code: ([A-Z0-9]{4})',output.getvalue()).group(1)
  result=self.client.post('/join',{'code':code,'email':'owner@example.com','password':'Local-only-passphrase-849!','password_confirm':'Local-only-passphrase-849!'},secure=True)
  self.assertRedirects(result,'/',fetch_redirect_response=False)
  self.assertEqual(User.objects.get().role,'admin')
  self.assertTrue(self.client.get('/users/session',secure=True).json()['admin'])
 def test_ordinary_user_sign_in_goes_to_dashboard(self):
  User.objects.create_user(username='person',email='person@example.com',role='user',display_name='Person',password='Local-only-passphrase-849!')
  result=self.client.post('/users/sign-in',{'email':'person@example.com','password':'Local-only-passphrase-849!'},secure=True)
  self.assertRedirects(result,'/',fetch_redirect_response=False)
  self.assertEqual(self.client.get('/users/add',secure=True).status_code,403)

class NavigationTests(TestCase):
 def test_public_forms_have_no_home_or_signout(self):
  for url in ['/users/sign-in','/join']:
   response=self.client.get(url,secure=True)
   self.assertNotContains(response,'>Home</a>')
   self.assertNotContains(response,'action="/users/sign-out"')
 def test_account_ready_has_one_clear_dashboard_action(self):
  user=User.objects.create_user(username='ready',email='ready@example.com',role='user',display_name='Ready',password='Local-only-passphrase-849!',vault_user_id='dummy-native-id',vault_items={'pivault':'dummy-item'},service_access={'passwords':'isolated'})
  self.client.force_login(user)
  from unittest.mock import patch
  from portal.runtime import PUBLIC_URLS
  with patch.dict(PUBLIC_URLS,{'passwords':'https://192.168.4.90'}):response=self.client.get('/users/complete',secure=True)
  self.assertContains(response,'Go to my Dashboard',count=1)
  self.assertContains(response,'Install Password Manager')
  # The server address row stays: Bitwarden clients need it for Self-hosted sign-in.
  self.assertContains(response,'id="vault-server"')
  for removed in ['Open my vault','>Home</a>','Sign out']:
   self.assertNotContains(response,removed)
