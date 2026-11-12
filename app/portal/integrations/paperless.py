"""Paperless-ngx 3.3.3 per-user accounts through its native REST API.

Documents are per user only: each enrolled user gets an ordinary Paperless account
with the standard document permissions. Paperless makes the uploader the owner of a new document, so private
documents stay private. The provisioning token belongs to a superuser and is only
held in private server configuration. Accounts are verified by reading them back
and by logging in with the exact password. Failed enrollments delete the new account.
"""
from urllib.parse import quote

from django.core.exceptions import ValidationError

from .native_http import CreatedAccount, JSONClient, NativeError


# Paperless looks permissions up by bare Django codename (for example view_document), not app.codename.
USER_PERMISSIONS = (
    'view_document', 'add_document',
    'change_document', 'delete_document',
    'view_tag', 'view_correspondent', 'view_documenttype',
    'view_storagepath', 'view_customfield', 'view_uisettings',
    'change_uisettings',
)


class PaperlessCleanupRequired(ValidationError):
    def __init__(self, native_id):
        self.cleanup_failures = [{'service': 'documents', 'native_id': native_id}]
        super().__init__('Documents account setup failed; cleanup requires an admin.')


class PaperlessAdapter:
    modes = ('isolated',)

    def __init__(self, base_url, admin_token, *, client_factory=JSONClient):
        if not isinstance(admin_token, str) or not admin_token.strip():
            raise ValueError('A Documents provisioning token is required.')
        self.base_url = base_url
        self.admin_token = admin_token
        self.client_factory = client_factory

    def credential_username(self, user):
        return user.email.strip().lower()

    def _client(self, token=None):
        headers = {'Authorization': 'Token ' + token} if token else {}
        return self.client_factory('Documents', self.base_url, headers)

    def _delete(self, native_id):
        try:
            self._client(self.admin_token).request('DELETE', '/api/users/' + str(int(native_id)) + '/')
        except NativeError as error:
            if error.status != 404:
                raise

    def _existing_usernames(self, client):
        result = client.request('GET', '/api/users/?page_size=100')
        rows = result.get('results') if isinstance(result, dict) else result
        if not isinstance(rows, list) or any(not isinstance(v, dict) for v in rows):
            raise NativeError('Documents')
        return [str(v.get('username', '')).casefold() for v in rows]

    def provision(self, *, user, password, mode):
        if mode not in self.modes:
            raise ValidationError('Documents does not support that access mode.')
        name = self.credential_username(user)
        client = self._client(self.admin_token)
        if name.casefold() in self._existing_usernames(client):
            raise ValidationError('A Documents account already uses that email. An admin must resolve it before joining.')
        expected = set(USER_PERMISSIONS)
        native_id = None
        try:
            try:
                result = client.request('POST', '/api/users/', {
                    'username': name, 'email': name, 'password': password,
                    'is_active': True, 'is_staff': False, 'is_superuser': False,
                    'user_permissions': sorted(expected),
                })
            except NativeError as error:
                if error.uncertain:
                    # A lost response does not establish ownership of any returned row.
                    raise PaperlessCleanupRequired('username:' + name) from None
                raise
            if not isinstance(result, dict) or type(result.get('id')) is not int or result['id'] <= 0:
                raise PaperlessCleanupRequired('username:' + name)
            native_id = str(result['id'])
            actual = client.request('GET', '/api/users/' + native_id + '/')
            granted = {str(p) for p in actual.get('user_permissions', [])} if isinstance(actual, dict) else None
            if (not isinstance(actual, dict) or str(actual.get('id')) != native_id
                    or actual.get('username') != name or actual.get('is_superuser') is not False
                    or granted != expected):
                raise NativeError('Documents', uncertain=True)
            # Verify the exact password through the same token endpoint the app uses.
            token = self._client().request('POST', '/api/token/', {'username': name, 'password': password})
            if not isinstance(token, dict) or not isinstance(token.get('token'), str) or not token['token']:
                raise NativeError('Documents', uncertain=True)
        except Exception:
            if native_id is not None:
                try:
                    self._delete(native_id)
                except Exception:
                    raise PaperlessCleanupRequired(native_id) from None
            raise
        return CreatedAccount('documents', native_id, lambda: self._delete(native_id))
