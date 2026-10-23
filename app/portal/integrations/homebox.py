"""Homebox 0.27 account provisioning through its native registration API.

Staged until private routing and registration restrictions are installed.
Shared requires an invitation for the explicitly configured household. Isolated
uses Homebox's own new-household behavior, never a new container.
"""
from django.core.exceptions import ValidationError
from .native_http import CreatedAccount, JSONClient, NativeError


class HomeboxCleanupRequired(ValidationError):
    def __init__(self, native_id):
        self.cleanup_failures=[{'service':'inventory','native_id':native_id}]
        super().__init__('Inventory account setup failed; cleanup requires an admin.')


class HomeboxAdapter:
    def credential_username(self, user):
        return user.email

    def __init__(self, base_url, *, allow_isolated=False, shared_token='',
                 shared_group_id='', client=None, authenticated_client=None):
        if bool(shared_token) != bool(shared_group_id):
            raise ValueError('A shared household requires its invitation and group ID.')
        self.modes=tuple(mode for mode,enabled in (
            ('shared',bool(shared_token)),('isolated',allow_isolated)) if enabled)
        self.shared_token=shared_token
        self.shared_group_id=shared_group_id
        self.client=client or JSONClient('Inventory',base_url)
        self.authenticated_client=authenticated_client or (
            lambda token:JSONClient('Inventory',base_url,{'Authorization':token}))

    def provision(self, *, user, password, mode):
        if mode not in self.modes:
            raise ValidationError('Inventory does not support that access mode.')
        payload={'name':user.display_name,'email':user.email,'password':password}
        if mode=='shared':
            payload['token']=self.shared_token
        # A failed/conflicting registration must never fall through to login:
        # that would risk adopting/deleting a pre-existing account.
        try:
            self.client.request('POST','/api/v1/users/register',payload)
        except NativeError as error:
            if error.uncertain:
                raise HomeboxCleanupRequired(user.email) from None
            raise
        native_id=user.email
        receipt=None
        try:
            result=self.client.request('POST','/api/v1/users/login',{
                'username':user.email,'password':password,'stayLoggedIn':False})
            token=result.get('token') if isinstance(result,dict) else None
            if not isinstance(token,str) or not token.startswith('Bearer ') or any(c in token for c in '\r\n'):
                raise NativeError('Inventory')
            authenticated=self.authenticated_client(token)
            identity=authenticated.request('GET','/api/v1/users/self')
            item=identity.get('item') if isinstance(identity,dict) else None
            if (not isinstance(item,dict) or not isinstance(item.get('id'),str)
                or not item['id'] or str(item.get('email','')).casefold()!=user.email.casefold()
                or item.get('name')!=user.display_name):
                raise NativeError('Inventory')
            native_id=item['id']
            receipt=CreatedAccount('inventory',native_id,
                lambda:authenticated.request('DELETE','/api/v1/users/self'))
            group=item.get('defaultGroupId')
            if (not group or (mode=='shared' and group!=self.shared_group_id)
                or (mode=='isolated' and self.shared_group_id and group==self.shared_group_id)):
                raise NativeError('Inventory')
        except Exception as error:
            if receipt is None:
                raise HomeboxCleanupRequired(native_id) from None
            try:
                receipt.rollback()
            except Exception:
                raise HomeboxCleanupRequired(native_id) from None
            raise error
        return receipt
