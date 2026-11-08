"""Lychee 7.12.0 per-user accounts through its admin API.

Each enrolled user gets an ordinary, non-administrator Lychee account, so the albums
and photos that user creates belong to that user. Lychee administrators can see every
album and photo, so Per user is not private from them. The provisioning token belongs
to a Lychee administrator and is held only in private server configuration. Accounts
are read back and checked by logging in with the exact password. Failed enrollments
delete the new account.
"""
from functools import partial

from django.core.exceptions import ValidationError

from .native_http import CreatedAccount, JSONClient, NativeError


class LycheeCleanupRequired(ValidationError):
    def __init__(self, native_id):
        self.cleanup_failures = [{'service': 'photos', 'native_id': native_id}]
        super().__init__('Photos account setup failed; cleanup requires an admin.')


def _rows(result):
    rows = result.get('data', result) if isinstance(result, dict) else result
    if not isinstance(rows, list) or any(not isinstance(v, dict) for v in rows):
        raise NativeError('Photos')
    return rows


class LycheeAdapter:
    modes = ('isolated',)

    def __init__(self, base_url, admin_token, *, client_factory=JSONClient):
        if not isinstance(admin_token, str) or not admin_token.strip():
            raise ValueError('A Photos provisioning token is required.')
        self.base_url = base_url
        self.admin_token = admin_token
        self.client_factory = client_factory

    def credential_username(self, user):
        return user.email.strip().lower()

    def _client(self, token=None):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        return self.client_factory('Photos', self.base_url, headers)

    def _delete(self, native_id):
        try:
            self._client(self.admin_token).request('DELETE', '/api/v2/UserManagement', {'id': int(native_id)})
        except NativeError as error:
            if error.status != 404:
                raise

    def provision(self, *, user, password, mode):
        if mode not in self.modes:
            raise ValidationError('Photos does not support that access mode.')
        name = self.credential_username(user)
        client = self._client(self.admin_token)
        taken = [str(row.get('username', '')).casefold() for row in _rows(client.request('GET', '/api/v2/UserManagement'))]
        if name.casefold() in taken:
            raise ValidationError('A Photos account already uses that email. An admin must resolve it before joining.')
        native_id = None
        try:
            try:
                result = client.request('POST', '/api/v2/UserManagement', {
                    'username': name, 'password': password,
                    'may_upload': True, 'may_edit_own_settings': False, 'may_administrate': False,
                })
            except NativeError as error:
                if error.uncertain:
                    # A lost response does not establish ownership of any returned row.
                    raise LycheeCleanupRequired('username:' + name) from None
                raise
            created = result.get('data', result) if isinstance(result, dict) else None
            if not isinstance(created, dict) or type(created.get('id')) is not int or created['id'] <= 0:
                raise LycheeCleanupRequired('username:' + name)
            native_id = created['id']
            actual = [row for row in _rows(client.request('GET', '/api/v2/UserManagement')) if row.get('id') == native_id]
            if len(actual) != 1 or actual[0].get('username') != name or actual[0].get('may_administrate') is not False:
                raise NativeError('Photos', uncertain=True)
            # Verify the exact password through the same login the app uses.
            self._client().request('POST', '/api/v2/Auth::login', {'username': name, 'password': password})
        except Exception:
            if native_id is not None:
                try:
                    self._delete(native_id)
                except Exception:
                    raise LycheeCleanupRequired(native_id) from None
            raise
        return CreatedAccount('photos', str(native_id), partial(self._delete, native_id))
