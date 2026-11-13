"""Jellyfin account provisioning via UserApi, not authentication-database edits.

This adapter is not activated until the deployment has supplied a private API
credential and closed the original direct-access path. It supports the existing
shared video library. Private-library provisioning is intentionally absent.
"""
from urllib.parse import quote
from django.core.exceptions import ValidationError
from .native_http import CreatedAccount, JSONClient, NativeError


class JellyfinAdapter:
    modes=('shared',)

    def credential_username(self, user):
        return user.display_name

    def __init__(self, base_url, api_key, client=None):
        if not api_key or '\r' in api_key or '\n' in api_key:
            raise ValueError('A Jellyfin API credential is required.')
        # Jellyfin 12.2 declares API keys as the Authorization header and rejects X-Emby-Token with 401.
        self.client=client or JSONClient('Videos',base_url,{'Authorization':f'MediaBrowser Token="{api_key}"'})

    def list_accounts(self):
        rows = self.client.request('GET', '/Users')
        if not isinstance(rows, list) or any(not isinstance(v, dict) for v in rows):
            raise NativeError('Videos')
        return [str(v.get('Name', '')) for v in rows]

    def provision(self, *, user, password, mode):
        if mode not in self.modes:
            raise ValidationError('Videos does not support that access mode.')
        if mode == 'shared':
            # One shared login serves everyone; the vault copy supplies it, so no per-person account is made.
            return None
        name=user.display_name
        existing=self.client.request('GET','/Users')
        if not isinstance(existing,list):
            raise NativeError('Videos')
        if any(str(value.get('Name','')).casefold()==name.casefold() for value in existing):
            # Do not reset, adopt, demote or overwrite an existing native account.
            raise ValidationError('A Videos account already uses that name. The admin must choose a different name.')
        try:
            value=self.client.request('POST','/Users/New',{'Name':name,'Password':password})
        except NativeError as error:
            if error.uncertain:
                raise JellyfinCleanupRequired('name:'+name) from None
            raise
        if not isinstance(value,dict) or not isinstance(value.get('Id'),str) or not value['Id']:
            raise JellyfinCleanupRequired('name:'+name)
        native_id=value['Id']
        path='/Users/'+quote(native_id,safe='')
        receipt=CreatedAccount('videos',native_id,lambda:self.client.request('DELETE',path))
        try:
            policy=value.get('Policy')
            if not isinstance(policy,dict):
                raise NativeError('Videos',uncertain=True)
            policy={**policy,'IsAdministrator':False,'EnableAllFolders':True}
            self.client.request('POST',path+'/Policy',policy)
        except Exception as error:
            try:
                receipt.rollback()
            except Exception:
                raise JellyfinCleanupRequired(native_id) from None
            raise error
        return receipt


class JellyfinCleanupRequired(ValidationError):
    def __init__(self, native_id):
        self.cleanup_failures=[{'service':'videos','native_id':native_id}]
        super().__init__('Videos account setup failed; cleanup requires an admin.')
