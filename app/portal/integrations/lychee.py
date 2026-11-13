"""Lychee access through one shared login, read from the admin's vault.

Photos is shared or none. Everyone who joins signs in to the same Lychee account, so every
user sees and uploads to the same albums. The shared account is created in Lychee by the
admin and saved in the admin's vault. Joining copies that entry into each new vault.
The provisioning token belongs to a Lychee administrator and is held only in private server
configuration; it is used here only to list existing accounts.
"""
from django.core.exceptions import ValidationError

from .native_http import JSONClient, NativeError


def _rows(result):
    rows = result.get('data', result) if isinstance(result, dict) else result
    if not isinstance(rows, list) or any(not isinstance(v, dict) for v in rows):
        raise NativeError('Photos')
    return rows


class LycheeAdapter:
    modes = ('shared',)

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

    def list_accounts(self):
        rows = _rows(self._client(self.admin_token).request('GET', '/api/v2/UserManagement'))
        return [str(row.get('username', '')) for row in rows]

    def provision(self, *, user, password, mode):
        if mode not in self.modes:
            raise ValidationError('Photos is shared or none. Per-user Photos is not offered.')
        # One shared account serves everyone; the vault copy supplies it, so no per-person account is made.
        return None
