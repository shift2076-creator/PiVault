from types import SimpleNamespace
from unittest.mock import Mock
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from portal.integrations.homebox import HomeboxAdapter,HomeboxCleanupRequired
from portal.integrations.native_http import NativeError


class HomeboxTests(SimpleTestCase):
    def setup_adapter(self,group='private-household'):
        self.user=SimpleNamespace(display_name='Alex',email='alex@example.com')
        self.client=Mock()
        self.client.request.side_effect=[None,{'token':'Bearer new-session'}]
        self.auth=Mock()
        self.auth.request.return_value={'item':{'id':'new-user','name':'Alex',
            'email':self.user.email,'defaultGroupId':group}}
        self.factory=Mock(return_value=self.auth)
        return HomeboxAdapter('http://homebox:7745',allow_isolated=True,
            shared_token='household-invitation',shared_group_id='shared-household',
            client=self.client,authenticated_client=self.factory)

    def test_private_household_uses_exact_name_email_password(self):
        adapter=self.setup_adapter()
        receipt=adapter.provision(user=self.user,password='chosen-password',mode='isolated')
        self.assertEqual(self.client.request.call_args_list[0].args,(
            'POST','/api/v1/users/register',{'name':'Alex','email':'alex@example.com','password':'chosen-password'}))
        self.factory.assert_called_once_with('Bearer new-session')
        receipt.rollback()
        self.auth.request.assert_called_with('DELETE','/api/v1/users/self')

    def test_shared_uses_native_invitation_and_checks_group(self):
        adapter=self.setup_adapter('shared-household')
        adapter.provision(user=self.user,password='pw',mode='shared')
        self.assertEqual(self.client.request.call_args_list[0].args[2]['token'],'household-invitation')

    def test_wrong_household_removes_only_new_user(self):
        adapter=self.setup_adapter('wrong-household')
        with self.assertRaises(NativeError):
            adapter.provision(user=self.user,password='pw',mode='shared')
        self.auth.request.assert_called_with('DELETE','/api/v1/users/self')

    def test_failed_registration_never_logs_into_existing_user(self):
        adapter=self.setup_adapter()
        self.client.request.side_effect=NativeError('Inventory',409)
        with self.assertRaises(NativeError):
            adapter.provision(user=self.user,password='pw',mode='isolated')
        self.assertEqual(self.client.request.call_count,1)
        self.factory.assert_not_called()

    def test_lost_registration_response_requires_admin_review(self):
        adapter=self.setup_adapter()
        self.client.request.side_effect=NativeError('Inventory',uncertain=True)
        with self.assertRaises(HomeboxCleanupRequired) as caught:
            adapter.provision(user=self.user,password='never-store',mode='isolated')
        self.assertEqual(caught.exception.cleanup_failures,[{
            'service':'inventory','native_id':'alex@example.com'}])
        self.assertNotIn('never-store',str(caught.exception))

    def test_login_failure_cannot_consume_invite_silently(self):
        adapter=self.setup_adapter()
        self.client.request.side_effect=[None,NativeError('Inventory',503)]
        with self.assertRaises(HomeboxCleanupRequired):
            adapter.provision(user=self.user,password='pw',mode='isolated')
        self.auth.request.assert_not_called()

    def test_identity_mismatch_never_deletes_unverified_account(self):
        adapter=self.setup_adapter()
        self.auth.request.return_value={'item':{'id':'other-user','email':'other@example.com','name':'Other'}}
        with self.assertRaises(HomeboxCleanupRequired):
            adapter.provision(user=self.user,password='pw',mode='isolated')
        self.assertEqual(self.auth.request.call_count,1)

    def test_unconfigured_modes_omitted_and_rejected(self):
        adapter=HomeboxAdapter('http://homebox:7745',client=Mock())
        self.assertEqual(adapter.modes,())
        with self.assertRaises(ValidationError):
            adapter.provision(user=None,password='pw',mode='isolated')
        adapter.client.request.assert_not_called()

    def test_partial_shared_configuration_rejected(self):
        with self.assertRaises(ValueError):
            HomeboxAdapter('http://homebox:7745',shared_token='token')
