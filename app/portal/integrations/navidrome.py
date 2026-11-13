"""Navidrome 0.64.2 account enrollment through its native HTTP API.

The installer supplies a dedicated provisioning administrator. End users receive
their own native account and signup password; existing accounts are never adopted.
Shared access uses explicitly configured libraries. Per-user access gives each
non-admin account one private library under a configured root. No database or
password-format writes.
"""
import hashlib
import os
import re
from urllib.parse import quote

from django.core.exceptions import ValidationError

from .native_http import CreatedAccount, JSONClient, NativeError


class NavidromeCleanupRequired(ValidationError):
    def __init__(self, native_id):
        self.cleanup_failures = [{'service': 'music', 'native_id': native_id}]
        super().__init__('Music account setup failed; cleanup requires an admin.')


class NavidromeAdapter:
    modes = ('shared',)

    def __init__(self, base_url, admin_username, admin_password, *,
                 library_ids=None, private_root=None, client_factory=JSONClient):
        if not isinstance(admin_username, str) or not admin_username.strip():
            raise ValueError('A Music provisioning administrator is required.')
        if not isinstance(admin_password, str) or not admin_password:
            raise ValueError('A Music provisioning password is required.')
        if library_ids is not None and (not isinstance(library_ids, (list, tuple))
                or not library_ids or any(type(i) is not int or i <= 0 for i in library_ids)):
            raise ValueError('Music library IDs must be a nonempty list of positive integers.')
        if private_root is not None:
            # Shared scope must be explicit, or private libraries would be shared and searched.
            if library_ids is None:
                raise ValueError('Per-user Music folders need explicit shared library IDs.')
            if (not isinstance(private_root, str) or not private_root.startswith('/')
                    or any(part in ('', '.', '..') for part in private_root.split('/')[1:])):
                raise ValueError('The Music private folder root must be an absolute path.')
            self.modes = ('shared', 'isolated')
        self.base_url = base_url
        self.admin_username = admin_username
        self.admin_password = admin_password
        self.library_ids = None if library_ids is None else frozenset(library_ids)
        self.private_root = private_root
        self.client_factory = client_factory

    def credential_username(self, user):
        return user.email.strip().lower()

    def _client(self, headers=None):
        return self.client_factory('Music', self.base_url, headers or {})

    def _admin_client(self):
        result = self._client().request('POST', '/auth/login', {
            'username': self.admin_username, 'password': self.admin_password,
        })
        if (not isinstance(result, dict) or result.get('isAdmin') is not True
                or not isinstance(result.get('token'), str) or not result['token']
                or '\r' in result['token'] or '\n' in result['token']):
            raise NativeError('Music')
        return self._client({'X-ND-Authorization': 'Bearer ' + result['token']})

    def list_accounts(self):
        rows = self._admin_client().request('GET', '/api/user')
        if not isinstance(rows, list) or any(not isinstance(v, dict) for v in rows):
            raise NativeError('Music')
        return [str(v.get('userName', '')) for v in rows]

    def _delete(self, native_id):
        # Log in again rather than retaining an expiring token in the receipt.
        try:
            self._admin_client().request('DELETE', '/api/user/' + quote(native_id, safe=''))
        except NativeError as error:
            if error.status != 404:
                raise

    def _delete_library(self, library_id):
        try:
            self._admin_client().request('DELETE', '/api/library/' + str(library_id))
        except NativeError as error:
            if error.status != 404:
                raise

    def _rollback(self, native_id, library_id):
        try:
            if native_id is not None:
                self._delete(native_id)
        finally:
            if library_id is not None:
                self._delete_library(library_id)

    def _private_folder(self, name):
        # The folder is created by the portal (read-write mount); Navidrome reads it read-only.
        if not os.path.isdir(self.private_root):
            raise ValidationError('Music private folder storage is unavailable. An admin must resolve it before joining.')
        slug = re.sub(r'[^a-z0-9._-]', '-', name)[:60].strip('.-') or 'account'
        digest = hashlib.sha256(name.encode()).hexdigest()[:10]
        folder = os.path.join(self.private_root, f'{slug}-{digest}')
        try:
            os.makedirs(folder, mode=0o750, exist_ok=True)
            leftover = os.listdir(folder)
        except OSError:
            raise ValidationError('Music private folder storage is unavailable. An admin must resolve it before joining.') from None
        if leftover:
            raise ValidationError('A private Music folder already has files for that account. An admin must resolve it before joining.')
        return folder

    def _create_private_library(self, client, name, folder):
        try:
            result = client.request('POST', '/api/library', {'name': 'Private: ' + name, 'path': folder})
        except NativeError as error:
            if error.uncertain:
                raise NavidromeCleanupRequired('library:' + name) from None
            raise
        if not isinstance(result, dict) or type(result.get('id')) is not int or result['id'] <= 0:
            # The library may exist without a known ID, so an admin must remove it.
            raise NavidromeCleanupRequired('library:' + name)
        return result['id']

    def provision(self, *, user, password, mode):
        if mode not in self.modes:
            raise ValidationError('Music does not support that access mode.')
        if mode == 'shared':
            # One shared login serves everyone; the vault copy supplies it, so no per-person account is made.
            return None
        name = self.credential_username(user)
        # Sub-accounts are never Navidrome administrators; only the main PiVault account is an admin.
        is_admin = False
        isolated = mode == 'isolated'
        client = self._admin_client()
        existing = client.request('GET', '/api/user')
        if not isinstance(existing, list) or any(not isinstance(v, dict) for v in existing):
            raise NativeError('Music')
        if any(str(v.get('userName', '')).casefold() == name.casefold() for v in existing):
            raise ValidationError('A Music account already uses that email. An admin must resolve it before joining.')
        if isolated:
            folder = self._private_folder(name)
        else:
            libraries = client.request('GET', '/api/library')
            if (not isinstance(libraries, list) or not libraries
                    or any(not isinstance(v, dict) or type(v.get('id')) is not int
                           or v['id'] <= 0 for v in libraries)):
                raise ValidationError('Music has no usable shared library. An admin must configure it before joining.')
            available = {v['id'] for v in libraries}
            selected = available if self.library_ids is None else self.library_ids
            if not selected.issubset(available):
                raise ValidationError('A configured shared Music library is unavailable.')
        native_id = None
        created_library = None
        try:
            if isolated:
                created_library = self._create_private_library(client, name, folder)
                selected = {created_library}
            try:
                result = client.request('POST', '/api/user', {
                    'userName': name, 'name': user.display_name,
                    'email': user.email.strip().lower(), 'password': password,
                    'isAdmin': is_admin,
                })
            except NativeError as error:
                if error.uncertain:
                    # A lost response does not establish ownership of any returned row.
                    raise NavidromeCleanupRequired('username:' + name) from None
                raise
            if (not isinstance(result, dict) or not isinstance(result.get('id'), str)
                    or not result['id']):
                raise NavidromeCleanupRequired('username:' + name)
            native_id = result['id']
            path = '/api/user/' + quote(native_id, safe='')
            if not is_admin:
                client.request('PUT', path + '/library', {'libraryIds': sorted(selected)})
            actual = client.request('GET', path)
            if (not isinstance(actual, dict) or actual.get('id') != native_id
                    or actual.get('userName') != name or actual.get('isAdmin') is not is_admin):
                raise NativeError('Music', uncertain=True)
            assigned = client.request('GET', path + '/library')
            expected = available if is_admin else selected
            if (not isinstance(assigned, list)
                    or any(not isinstance(v, dict) or type(v.get('id')) is not int for v in assigned)
                    or {v['id'] for v in assigned} != expected):
                raise NativeError('Music', uncertain=True)
            # Verify the exact password through the same login endpoint as the UI.
            authenticated = self._client().request('POST', '/auth/login', {
                'username': name, 'password': password,
            })
            if (not isinstance(authenticated, dict) or authenticated.get('id') != native_id
                    or authenticated.get('isAdmin') is not is_admin):
                raise NativeError('Music', uncertain=True)
        except Exception:
            try:
                self._rollback(native_id, created_library)
            except Exception:
                raise NavidromeCleanupRequired(
                    native_id if native_id is not None else 'library:' + str(created_library)) from None
            raise
        return CreatedAccount('music', native_id, lambda: self._rollback(native_id, created_library))
