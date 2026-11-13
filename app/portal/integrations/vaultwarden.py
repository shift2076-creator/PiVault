"""Native Vaultwarden 1.37 registration and cipher APIs, with browser-encrypted data."""
import base64
import hashlib
import hmac
import json
import logging
from http.cookies import SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request
import uuid
from django.core.exceptions import ValidationError
from .native_http import CreatedAccount, JSONClient, NativeError
from ..setup_logging import event

ITERATIONS = 600000


def enc_string(value, max_bytes=4096):
    try:
        if not isinstance(value, str) or not value.startswith('2.') or len(value) > max_bytes * 2 + 128:
            raise ValueError()
        iv, cipher, mac = [base64.b64decode(part, validate=True) for part in value[2:].split('|')]
        if len(iv) != 16 or len(mac) != 32 or not cipher or len(cipher) % 16 or len(cipher) > max_bytes:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError('The encrypted vault setup is invalid.') from None
    return value


def validate_bundle(bundle, password, email, expected):
    if not isinstance(bundle, dict) or set(bundle) != {'masterPasswordHash', 'key', 'keys', 'folder', 'items'}:
        raise ValidationError('The encrypted vault setup is incomplete.')
    master = hashlib.pbkdf2_hmac('sha256', password.encode(), email.encode(), ITERATIONS, dklen=32)
    expected_hash = base64.b64encode(hashlib.pbkdf2_hmac('sha256', master, password.encode(), 1, dklen=32)).decode()
    if not isinstance(bundle['masterPasswordHash'], str) or not hmac.compare_digest(expected_hash, bundle['masterPasswordHash']):
        raise ValidationError('The vault must use the same password as PiVault.')
    enc_string(bundle['key'], 80); enc_string(bundle['folder'], 80)
    keys = bundle['keys']
    if not isinstance(keys, dict) or set(keys) != {'publicKey', 'encryptedPrivateKey'}:
        raise ValidationError('The encrypted vault keys are incomplete.')
    enc_string(keys['encryptedPrivateKey'], 4096)
    try:
        public = base64.b64decode(keys['publicKey'], validate=True)
        if not 250 <= len(public) <= 1024:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError('The vault public key is invalid.') from None
    if not isinstance(bundle['items'], dict) or set(bundle['items']) != set(expected):
        raise ValidationError('Vault entries must match the selected tools.')
    for item in bundle['items'].values():
        if not isinstance(item, dict) or set(item) != {'name', 'username', 'password', 'uri'}:
            raise ValidationError('An encrypted login entry is incomplete.')
        for key in item:
            enc_string(item[key], 4096)
    return bundle


class VaultHTTP(JSONClient):
    def form(self, path, values):
        request = Request(self.base_url + path, data=urlencode(values).encode(), method='POST',
            headers={**self.headers, 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError()
                return response.headers, json.loads(raw) if raw else None
        except HTTPError as error:
            raise NativeError('Password Manager', status=error.code) from None
        except (URLError, OSError, ValueError):
            raise NativeError('Password Manager', uncertain=True) from None

    def admin(self, token):
        # Native /admin login returns HTML + Set-Cookie, not an API bearer token.
        request = Request(self.base_url + '/admin', data=urlencode({'token': token}).encode(),
            method='POST', headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except HTTPError as error:
                if error.code not in (302, 303):
                    raise
                response = error
            with response:
                cookie = SimpleCookie()
                for value in response.headers.get_all('Set-Cookie', []):
                    cookie.load(value)
                jwt = cookie['VW_ADMIN'].value
                if not jwt or any(c in jwt for c in '\r\n;'):
                    raise ValueError()
            return VaultHTTP('Password Manager', self.base_url, {'Cookie': 'VW_ADMIN=' + jwt})
        except (HTTPError, URLError, OSError, ValueError, KeyError):
            raise NativeError('Password Manager') from None


class VaultCleanupRequired(ValidationError):
    def __init__(self, native_id):
        self.cleanup_failures = [{'service': 'passwords', 'native_id': native_id}]
        super().__init__('Password Manager setup failed; an Admin must review incomplete cleanup.')


class VaultwardenAdapter:
    modes = ('isolated',)
    requires_browser_bundle = True

    def credential_username(self, user):
        return user.email

    def __init__(self, base_url, admin_token, public_url, client=None):
        if not admin_token:
            raise ValueError('A private Vaultwarden administration credential is required.')
        self.client = client or VaultHTTP('Password Manager', base_url)
        self.admin_token = admin_token
        self.public_url = public_url

    def list_accounts(self):
        rows = self.client.admin(self.admin_token).request('GET', '/admin/users')
        if not isinstance(rows, list) or any(not isinstance(v, dict) for v in rows):
            raise NativeError('Password Manager')
        return [str(v.get('email', '')) for v in rows]

    def provision(self, *, user, password, mode, bundle):
        if mode != 'isolated':
            raise ValidationError('Password Manager is per user only.')
        event('native_stage', service='passwords', stage='admin_auth')
        admin = self.client.admin(self.admin_token)
        event('native_stage', service='passwords', stage='account_lookup')
        path = '/admin/users/by-mail/' + quote(user.email, safe='')
        try:
            admin.request('GET', path)
        except NativeError as error:
            if error.status != 404:
                raise
        else:
            raise ValidationError('A vault already uses that email. It was not changed.')
        try:
            event('native_stage', service='passwords', stage='invite')
            invited = admin.request('POST', '/admin/invite', {'email': user.email})
        except NativeError as error:
            if error.uncertain:
                raise VaultCleanupRequired(user.email) from None
            raise
        native_id = invited.get('id') if isinstance(invited, dict) else None
        if not isinstance(native_id, str) or not native_id:
            raise VaultCleanupRequired(user.email)
        receipt = CreatedAccount('passwords', native_id,
            lambda: admin.request('POST', '/admin/users/' + quote(native_id, safe='') + '/delete', {}))
        try:
            event('native_stage', service='passwords', stage='verification')
            token = self.client.request('POST', '/identity/accounts/register/send-verification-email',
                {'email': user.email, 'name': user.display_name})
            if not isinstance(token, str) or not token:
                raise ValidationError('This vault requires email verification; offline account setup is unavailable.')
            event('native_stage', service='passwords', stage='registration')
            self.client.request('POST', '/identity/accounts/register/finish', {
                'email': user.email, 'emailVerificationToken': token, 'kdf': 0, 'kdfIterations': ITERATIONS,
                'masterPasswordHash': bundle['masterPasswordHash'], 'key': bundle['key'], 'keys': bundle['keys']})
            event('native_stage', service='passwords', stage='login_verification')
            _, login = self.client.form('/identity/connect/token', {
                'grant_type': 'password', 'client_id': 'web', 'scope': 'api offline_access',
                'username': user.email, 'password': bundle['masterPasswordHash'],
                'deviceType': '9', 'deviceIdentifier': str(uuid.uuid4()), 'deviceName': 'PiVault enrollment'})
            bearer = login.get('access_token') if isinstance(login, dict) else None
            if not isinstance(bearer, str) or not bearer or any(c in bearer for c in '\r\n'):
                raise NativeError('Password Manager')
            authenticated = VaultHTTP('Password Manager', self.client.base_url, {'Authorization': 'Bearer ' + bearer})
            event('native_stage', service='passwords', stage='profile_verification')
            profile = authenticated.request('GET', '/api/accounts/profile')
            if (not isinstance(profile, dict) or profile.get('id') != native_id
                or str(profile.get('email', '')).casefold() != user.email.casefold()):
                raise NativeError('Password Manager')
            event('native_stage', service='passwords', stage='folder')
            folder = authenticated.request('POST', '/api/folders', {'name': bundle['folder']})
            folder_id = folder.get('id') if isinstance(folder, dict) else None
            if not isinstance(folder_id, str) or not folder_id:
                raise NativeError('Password Manager')
            ids = {}
            for service, item in bundle['items'].items():
                event('native_stage', service='passwords', stage='credential_' + service)
                cipher = authenticated.request('POST', '/api/ciphers', {
                    'type': 1, 'name': item['name'], 'encryptedFor': native_id, 'folderId': folder_id, 'organizationId': None,
                    'favorite': False, 'reprompt': 0, 'login': {'username': item['username'],
                    'password': item['password'], 'uris': [{'uri': item['uri'], 'match': 1}]}})
                if not isinstance(cipher, dict) or not isinstance(cipher.get('id'), str) or not cipher['id']:
                    raise NativeError('Password Manager')
                ids[service] = cipher['id']
            user.vault_user_id = native_id; user.vault_items = ids
        except Exception as error:
            event('native_failed', service='passwords', error=error, level=logging.ERROR)
            try:
                event('cleanup_started', service='passwords')
                receipt.rollback()
                event('cleanup_succeeded', service='passwords')
            except Exception as cleanup_error:
                event('cleanup_failed', service='passwords', error=cleanup_error, level=logging.ERROR)
                raise VaultCleanupRequired(native_id) from None
            raise
        return receipt
