import io
from types import SimpleNamespace
from unittest.mock import Mock,patch
from urllib.error import HTTPError
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase,TestCase
from portal.integrations.jellyfin import JellyfinAdapter,JellyfinCleanupRequired
from portal.integrations.native_http import CreatedAccount,JSONClient,NativeError
from portal.services import ADAPTERS,provision


class JellyfinTests(SimpleTestCase):
    def adapter(self,responses):
        client=Mock()
        client.request.side_effect=responses
        return JellyfinAdapter('http://jellyfin:8096','test-api-key',client),client

    def test_creates_named_user_with_exact_password_and_regular_role(self):
        adapter,client=self.adapter([[],{'Id':'native-1','Policy':{'EnableContentDeletion':False}},None])
        user=SimpleNamespace(display_name='Alex',role='user')
        receipt=adapter.provision(user=user,password='same-user-password',mode='shared')
        calls=client.request.call_args_list
        self.assertEqual(calls[1].args,('POST','/Users/New',{'Name':'Alex','Password':'same-user-password'}))
        self.assertEqual(calls[2].args,('POST','/Users/native-1/Policy',{
            'EnableContentDeletion':False,'IsAdministrator':False,'EnableAllFolders':True}))
        self.assertEqual(receipt.native_id,'native-1')

    def test_admin_role_maps_to_native_admin(self):
        adapter,client=self.adapter([[],{'Id':'admin-1','Policy':{}},None])
        adapter.provision(user=SimpleNamespace(display_name='Owner',role='admin'),password='pw',mode='shared')
        self.assertTrue(client.request.call_args.args[2]['IsAdministrator'])

    def test_existing_account_is_never_overwritten(self):
        adapter,client=self.adapter([[{'Id':'existing','Name':'ALEX'}]])
        with self.assertRaises(ValidationError):
            adapter.provision(user=SimpleNamespace(display_name='Alex',role='user'),password='pw',mode='shared')
        self.assertEqual(client.request.call_count,1)

    def test_policy_failure_removes_only_newly_created_account(self):
        adapter,client=self.adapter([[],{'Id':'new-1','Policy':{}},NativeError('Videos',403),None])
        with self.assertRaises(NativeError):
            adapter.provision(user=SimpleNamespace(display_name='Alex',role='user'),password='pw',mode='shared')
        self.assertEqual(client.request.call_args.args,('DELETE','/Users/new-1'))

    def test_failed_cleanup_reports_native_id_without_credentials(self):
        adapter,client=self.adapter([[],{'Id':'new-1','Policy':{}},NativeError('Videos',403),NativeError('Videos',503)])
        with self.assertRaises(JellyfinCleanupRequired) as caught:
            adapter.provision(user=SimpleNamespace(display_name='Alex',role='user'),password='DO_NOT_REPORT',mode='shared')
        self.assertEqual(caught.exception.cleanup_failures,[{'service':'videos','native_id':'new-1'}])
        self.assertNotIn('DO_NOT_REPORT',str(caught.exception))

    def test_isolation_is_not_advertised_or_accepted(self):
        adapter,client=self.adapter([])
        self.assertEqual(adapter.modes,('shared',))
        with self.assertRaises(ValidationError):
            adapter.provision(user=SimpleNamespace(display_name='Alex',role='user'),password='pw',mode='isolated')
        client.request.assert_not_called()

    def test_sends_api_key_in_the_authorization_header_jellyfin_12_expects(self):
        adapter=JellyfinAdapter('http://jellyfin:8096','test-api-key')
        self.assertEqual(adapter.client.headers,{'Authorization':'MediaBrowser Token="test-api-key"'})

    def test_http_error_body_is_not_exposed(self):
        client=JSONClient('Videos','http://jellyfin:8096',{'X-Emby-Token':'DO_NOT_REPORT'})
        client.opener=Mock()
        client.opener.open.side_effect=HTTPError('http://jellyfin:8096/Users/New',403,'Forbidden',{},io.BytesIO(b'DO_NOT_REPORT'))
        with self.assertRaises(NativeError) as caught:
            client.request('POST','/Users/New',{'Password':'DO_NOT_REPORT'})
        self.assertEqual(caught.exception.status,403)
        self.assertNotIn('DO_NOT_REPORT',str(caught.exception))

    def test_credentials_cannot_be_encoded_in_upstream_url(self):
        with self.assertRaises(ValueError):
            JSONClient('Videos','http://user:password@jellyfin:8096')

    def test_lost_creation_response_requires_admin_review(self):
        adapter,client=self.adapter([[],NativeError('Videos',uncertain=True)])
        with self.assertRaises(JellyfinCleanupRequired) as caught:
            adapter.provision(user=SimpleNamespace(display_name='Alex',role='user'),password='pw',mode='shared')
        self.assertEqual(caught.exception.cleanup_failures,[{'service':'videos','native_id':'name:Alex'}])
        self.assertEqual(client.request.call_count,2)

    def test_malformed_creation_response_never_silently_retries(self):
        adapter,client=self.adapter([[],{}])
        with self.assertRaises(JellyfinCleanupRequired):
            adapter.provision(user=SimpleNamespace(display_name='Alex',role='user'),password='pw',mode='shared')
        self.assertEqual(client.request.call_count,2)


class CompensationTests(SimpleTestCase):
    def test_later_service_failure_rolls_back_prior_new_native_account(self):
        rollback=Mock()
        first=SimpleNamespace(modes=('shared',),provision=Mock(return_value=CreatedAccount('videos','new-1',rollback)))
        second=SimpleNamespace(modes=('shared',),provision=Mock(side_effect=ValidationError('Unavailable.')))
        user=SimpleNamespace(service_access={'videos':'shared','music':'shared'})
        with patch.dict(ADAPTERS,{'videos':first,'music':second}):
            with self.assertRaises(ValidationError), self.assertLogs('portal.setup', level='ERROR'):provision(user,'pw')
        rollback.assert_called_once()


class EnrollmentCleanupTests(TestCase):
    def test_account_save_failure_removes_native_account_and_returns_invitation(self):
        from django.db import IntegrityError
        from portal.invitations import issue
        from portal.models import Invitation,User
        admin=User.objects.create_user(username='owner',email='owner@example.com',
            password='Owner-password-123!',display_name='Owner',role='admin')
        rollback=Mock()
        adapter=SimpleNamespace(modes=('shared',),provision=Mock(
            return_value=CreatedAccount('videos','new-1',rollback)))
        with patch.dict(ADAPTERS,{'videos':adapter}):
            invitation,code=issue('Alex','user',{'videos':'shared'},admin)
            with patch.object(User,'save',side_effect=IntegrityError('database write failed')), self.assertLogs('portal.setup', level='ERROR'):
                response=self.client.post('/join',{'code':code,'email':'alex@example.com',
                    'password':'Do-not-store-this-password-123!','password_confirm':'Do-not-store-this-password-123!'},secure=True)
        self.assertContains(response,'Account setup is temporarily unavailable',status_code=503)
        rollback.assert_called_once()
        self.assertEqual(User.objects.count(),1)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.used_at)

    def test_failed_cleanup_preserves_admin_notice_after_enrollment_rollback(self):
        from portal.invitations import issue
        from portal.models import Notification,User
        admin=User.objects.create_user(username='owner',email='owner@example.com',
            password='Owner-password-123!',display_name='Owner',role='admin')
        rollback=Mock(side_effect=NativeError('Music',503))
        first=SimpleNamespace(modes=('shared',),provision=Mock(return_value=CreatedAccount('music','new-1',rollback)))
        second=SimpleNamespace(modes=('shared',),provision=Mock(side_effect=ValidationError('Videos unavailable.')))
        with patch.dict(ADAPTERS,{'music':first,'videos':second}), self.assertLogs('portal.setup', level='ERROR'):
            invitation,code=issue('Alex','user',{'videos':'shared','music':'shared'},admin)
            response=self.client.post('/join',{'code':code,'email':'alex@example.com',
                'password':'Do-not-store-this-password-123!','password_confirm':'Do-not-store-this-password-123!'},secure=True)
        self.assertContains(response,'admin must review',status_code=503)
        self.assertEqual(User.objects.count(),1)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.used_at)
        notification=Notification.objects.get(source='users')
        self.assertEqual(notification.details,{'accounts':[{'service':'music','native_id':'new-1'}]})
        self.assertNotIn('Do-not-store',str(notification.details)+notification.message)
