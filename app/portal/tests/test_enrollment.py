import re
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.utils import timezone
from portal.invitations import issue
from portal.models import Attempt, Invitation, User
from portal.services import normalize_access


class EnrollmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin-id', email='admin@example.com',
            password='Existing-good-password-928!', display_name='Owner', role='admin')

    def enroll(self, code, **extra):
        values = {'code':code, 'email':'alex@example.com', 'password':'A-long-new-passphrase-482!'}
        values.update(extra)
        values.setdefault('password_confirm', values['password'])
        return self.client.post('/join', values, secure=True)

    def test_guest_and_user_cannot_create_invitations(self):
        self.assertEqual(self.client.post('/users/add', {'name':'Attacker','role':'admin'}, secure=True).status_code,302)
        user = User.objects.create_user(username='ordinary',email='ordinary@example.com',role='user',display_name='Ordinary')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/users/add', secure=True).status_code,403)
        self.assertEqual(self.client.post('/users/add', {'name':'Attacker','role':'admin'}, secure=True).status_code,403)
        self.assertFalse(Invitation.objects.exists())

    def test_code_case_admin_name_and_role_are_enforced(self):
        invitation,code = issue('Alex', 'user', {}, self.admin)
        response = self.enroll(code.lower(), name='Unapproved',role='admin',service_books='shared')
        self.assertEqual(response.status_code,302)
        user = User.objects.get(email='alex@example.com')
        self.assertEqual(user.display_name,'Alex')
        self.assertEqual(user.role,'user')
        self.assertEqual(user.service_access['books'],'none')
        self.assertTrue(user.check_password('A-long-new-passphrase-482!'))
        self.assertNotIn('A-long-new-passphrase-482!',user.password)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.used_at)

    def test_replayed_code_cannot_create_second_account(self):
        _,code = issue('Alex','user',{},self.admin)
        self.enroll(code)
        self.enroll(code,email='second@example.com')
        self.assertFalse(User.objects.filter(email='second@example.com').exists())

    def test_expired_code_does_not_create_account(self):
        invitation,code = issue('Alex','user',{},self.admin)
        invitation.expires_at=timezone.now()-timedelta(seconds=1)
        invitation.save()
        self.assertContains(self.enroll(code),'Invalid or expired invitation code.',status_code=400)
        self.assertEqual(User.objects.count(),1)

    def test_bad_password_does_not_consume_code(self):
        invitation,code = issue('Alex','user',{},self.admin)
        self.enroll(code,password='123')
        invitation.refresh_from_db()
        self.assertIsNone(invitation.used_at)
        self.assertEqual(User.objects.count(),1)

    def test_email_case_does_not_create_duplicate_user(self):
        invitation,code=issue('Alex','user',{},self.admin)
        self.enroll(code,email='ADMIN@EXAMPLE.COM')
        self.assertEqual(User.objects.count(),1)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.used_at)

    def test_unconfigured_isolation_is_rejected_server_side(self):
        self.client.force_login(self.admin)
        response=self.client.post('/users/add',{'name':'Alex','role':'user','service_wiki':'isolated'},secure=True)
        self.assertContains(response,'integration is not configured',status_code=400)
        self.assertFalse(Invitation.objects.exists())

    def test_unavailable_modes_are_removed_not_disabled(self):
        from types import SimpleNamespace
        from portal.services import ADAPTERS
        self.client.force_login(self.admin)
        with patch.dict(ADAPTERS, {'books':SimpleNamespace(modes=('shared',))}):
            response=self.client.get('/users/add',secure=True)
        markup=response.content.decode()
        select=re.search(r'<select id="service-books".*?</select>',markup,re.S).group()
        self.assertIn('value="shared"',select)
        self.assertIn('value="none"',select)
        self.assertNotIn('value="isolated"',select)
        self.assertNotIn('disabled',select)
        # A service with native isolation keeps that mode.
        with patch.dict(ADAPTERS, {'passwords':SimpleNamespace(modes=('isolated',))}):
            response=self.client.get('/users/add',secure=True)
        select=re.search(r'<select id="service-passwords".*?</select>',response.content.decode(),re.S).group()
        self.assertIn('value="isolated"',select)
        self.assertNotIn('value="shared"',select)

    def test_service_failure_rolls_back_user_and_leaves_code_unused(self):
        invitation,code=issue('Alex','user',{},self.admin)
        with patch('portal.views.provision', side_effect=ValidationError('Service unavailable.')):
            response=self.enroll(code)
        self.assertContains(response,'Service unavailable.',status_code=400)
        self.assertEqual(User.objects.count(),1)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.used_at)

    def test_vault_adapter_receives_exact_join_password_and_assigned_name(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from portal.services import ADAPTERS
        adapter=SimpleNamespace(modes=('isolated',),provision=Mock())
        with patch.dict(ADAPTERS, {'passwords':adapter}):
            invitation,code=issue('Admin-assigned Alex','user',{'passwords':'isolated'},self.admin)
            response=self.enroll(code,password='Same-user-chosen-password-592!')
        self.assertEqual(response.status_code,302)
        adapter.provision.assert_called_once()
        args=adapter.provision.call_args.kwargs
        self.assertEqual(args['password'],'Same-user-chosen-password-592!')
        self.assertEqual(args['user'].display_name,'Admin-assigned Alex')
        self.assertEqual(args['mode'],'isolated')
        self.assertTrue(User.objects.get(email='alex@example.com').check_password('Same-user-chosen-password-592!'))

    def test_invitation_lifetime_and_code_format(self):
        before=timezone.now()
        invitation,code=issue('Alex','user',{},self.admin)
        self.assertRegex(code,r'^[A-Z0-9]{4}$')
        self.assertGreaterEqual(invitation.expires_at,before+timedelta(minutes=30))
        self.assertLess(invitation.expires_at,timezone.now()+timedelta(minutes=30))
        self.assertNotEqual(invitation.digest,code)

    def test_rate_limit_survives_new_client_session(self):
        for _ in range(12):
            self.enroll('!!!!')
        other=Client()
        response=other.post('/join',{'code':'!!!!'},secure=True)
        self.assertEqual(response.status_code,429)
        self.assertFalse(User.objects.filter(email='alex@example.com').exists())

    def test_other_apps_are_called_without_holding_the_database_lock(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from django.db import connection
        from portal.services import ADAPTERS
        depth=[]
        adapter=SimpleNamespace(modes=('shared',),provision=Mock(
            side_effect=lambda **kwargs: depth.append(len(connection.atomic_blocks))))
        base=len(connection.atomic_blocks)
        with patch.dict(ADAPTERS,{'videos':adapter}):
            invitation,code=issue('Alex','user',{'videos':'shared'},self.admin)
            response=self.enroll(code)
        self.assertEqual(response.status_code,302)
        self.assertEqual(depth,[base])
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.used_at)

    def test_one_device_cannot_lock_out_other_devices(self):
        from django.test import RequestFactory
        from portal.invitations import rate_limit
        def attempt(ip):
            return rate_limit(RequestFactory().post('/users/sign-in',HTTP_X_PIVAULT_CLIENT_IP=ip),'login')
        # 200 attempts spread across many devices exceed the old shared cap of 180.
        for number in range(200):
            attempt(f'10.0.{number // 10}.{number % 10}')
        self.assertTrue(all(attempt('192.168.4.200') for _ in range(12)))
        self.assertFalse(attempt('192.168.4.200'))
        self.assertTrue(attempt('192.168.4.77'))
        owner=Client().post('/users/sign-in',{'email':'admin@example.com','password':'Existing-good-password-928!'},
                            secure=True,HTTP_X_PIVAULT_CLIENT_IP='192.168.4.78')
        self.assertEqual(owner.status_code,302)

    def test_csrf_is_required(self):
        client=Client(enforce_csrf_checks=True)
        _,code=issue('Alex','user',{},self.admin)
        response=client.post('/join',{'code':code,'email':'alex@example.com','password':'A-long-new-passphrase-482!','password_confirm':'A-long-new-passphrase-482!'},secure=True)
        self.assertEqual(response.status_code,403)
        self.assertEqual(User.objects.count(),1)

    def test_join_code_starts_empty_and_has_no_name_explanation(self):
        response=self.client.get('/join?code=4KDP',secure=True)
        markup=response.content.decode()
        code_input=re.search(r'<input id="code"[^>]+>',markup).group()
        self.assertNotIn('value=',code_input)
        self.assertNotIn('Your name is assigned',markup)
        self.assertNotIn('Your services will be',markup)

    def test_qr_points_to_form_without_invitation_code(self):
        self.client.force_login(self.admin)
        response=self.client.post('/users/add',{'name':'Alex','role':'user'},secure=True)
        self.assertContains(response,'data:image/svg+xml;base64,')
        self.assertEqual(response.context['join_url'],'https://192.168.4.90:8443/join')
        self.assertNotIn(response.context['code'],response.context['join_url'])

    def test_gate_does_not_trust_client_role_headers(self):
        self.assertEqual(self.client.get('/internal/gate',HTTP_X_PIVAULT_ROLE='admin',secure=True).status_code,401)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/internal/gate',secure=True).status_code,204)
        self.assertEqual(self.client.get('/internal/gate?service=books',secure=True).status_code,403)

    def test_first_admin_command_refuses_existing_accounts(self):
        with self.assertRaises(CommandError):
            call_command('first_admin','Replacement',stdout=StringIO())

    def test_sign_in_case_insensitive_and_cookie_security(self):
        response=self.client.post('/users/sign-in',{'email':'ADMIN@EXAMPLE.COM','password':'Existing-good-password-928!'},secure=True)
        self.assertEqual(response.status_code,302)
        cookie=response.cookies['__Host-pivault']
        self.assertTrue(cookie['secure'])
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'],'Lax')
        self.assertEqual(response['Cache-Control'],'no-store, private')

    def test_authenticated_read_does_not_write_session(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.force_login(self.admin)
        self.client.get('/users/session', secure=True)  # First read stamps the monthly renewal.
        with CaptureQueriesContext(connection) as queries:
            self.client.get('/users/session', secure=True)
        self.assertFalse([q for q in queries if 'django_session' in q['sql'] and q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT'))])

    def test_session_cookie_is_renewed_monthly(self):
        self.client.force_login(self.admin)
        self.assertIn('__Host-pivault', self.client.get('/users/session', secure=True).cookies)
        self.assertNotIn('__Host-pivault', self.client.get('/users/session', secure=True).cookies)
        session = self.client.session
        session['_pivault_refreshed'] -= 31 * 24 * 60 * 60
        session.save()
        renewed = self.client.get('/users/session', secure=True).cookies['__Host-pivault']
        self.assertGreater(int(renewed['max-age']), 365 * 24 * 60 * 60)


class BootstrapTests(TestCase):
    def test_first_admin_is_local_command_not_public_race(self):
        response=self.client.post('/join',{'code':'AAAA','email':'owner@example.com','password':'First-good-passphrase-938!','password_confirm':'First-good-passphrase-938!'},secure=True)
        self.assertEqual(User.objects.count(),0)
        output=StringIO()
        call_command('first_admin','Owner',stdout=output)
        code=re.search(r'Code: ([A-Z0-9]{4})',output.getvalue()).group(1)
        response=self.client.post('/join',{'code':code,'email':'owner@example.com','password':'First-good-passphrase-938!','password_confirm':'First-good-passphrase-938!','role':'user','name':'Other'},secure=True)
        self.assertEqual(response.status_code,302)
        user=User.objects.get()
        self.assertEqual(user.role,'admin')
        self.assertEqual(user.display_name,'Owner')


class VaultRepairPageTests(TestCase):
    def test_repair_page_loads_its_script_externally_under_csp(self):
        admin=User.objects.create_user(username='repair-admin',email='repair@example.com',role='admin',display_name='Owner',vault_user_id='vault-id')
        self.client.force_login(admin)
        response=self.client.get('/users/repair-vault',secure=True)
        self.assertEqual(response.status_code,200)
        self.assertIn("script-src 'self'",response['Content-Security-Policy'])
        markup=response.content.decode()
        self.assertNotRegex(markup,r'<script(?![^>]*\bsrc=)')
        self.assertIn('<script src="/users/vault-repair.js" defer></script>',markup)
        script=self.client.get('/users/vault-repair.js',secure=True)
        self.assertEqual(script.status_code,200)
        self.assertEqual(script['Content-Type'],'application/javascript; charset=utf-8')
        self.assertIn("document.getElementById('unlock')",script.content.decode())
