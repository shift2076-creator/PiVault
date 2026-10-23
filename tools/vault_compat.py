"""Independent check: real Bitwarden CLI decrypts browser-generated vault data.

Uses a temporary localhost API fixture, dummy credentials and isolated CLI state.
Invoke with the absolute path to an official npm @bitwarden/cli build/bw.js.
"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import ssl
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit


def verify(cli_path):
    root = Path(__file__).resolve().parents[1] / 'app'
    email, password = 'vault-fixture@example.com', 'Fixture-only-passphrase-42!'
    item = {'name': 'PiVault fixture', 'username': 'Assigned name', 'uri': 'https://video.pivault.home.arpa'}
    node = 'require(process.argv[1]); let input=""; process.stdin.on("data",x=>input+=x); process.stdin.on("end",async()=>{const v=JSON.parse(input); console.log(JSON.stringify(await PiVaultVaultCrypto.create(v.email,v.password,v.items)));});'
    result = subprocess.run(['node', '-e', node, str(root / 'portal/vault-crypto.js')],
        input=json.dumps({'email': email, 'password': password, 'items': {'videos': item}}),
        capture_output=True, text=True, check=True, timeout=60)
    bundle = json.loads(result.stdout)
    master = hashlib.pbkdf2_hmac('sha256', password.encode(), email.encode(), 600000, 32)
    expected = base64.b64encode(hashlib.pbkdf2_hmac('sha256', master, password.encode(), 1, 32)).decode()
    assert expected == bundle['masterPasswordHash']
    uid = '974ced82-9048-4b8a-940d-5cabdb74b1e7'
    calls = []
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    access = encode({'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode({'sub': uid, 'email': email,
        'email_verified': True, 'name': 'Fixture', 'premium': False, 'exp': int(time.time()) + 3600,
        'iss': 'http://127.0.0.1', 'amr': ['Application'], 'scope': ['api', 'offline_access']}) + '.fixture'
    encrypted = bundle['items']['videos']
    account_keys = {'publicKeyEncryptionKeyPair': {'wrappedPrivateKey': bundle['keys']['encryptedPrivateKey'],
        'publicKey': bundle['keys']['publicKey'], 'object': 'publicKeyEncryptionKeyPair'}, 'object': 'privateKeys'}
    folder = '08c016c7-6316-47aa-9f2f-4d5af848f54f'
    cipher = {'id': '23cd70f7-c11b-44d9-8ec1-cbcc064145d5', 'organizationId': None,
        'folderId': folder, 'type': 1, 'name': encrypted['name'], 'notes': None, 'favorite': False,
        'reprompt': 0, 'key': None, 'login': {'username': encrypted['username'],
        'password': encrypted['password'], 'uris': [{'uri': encrypted['uri'], 'match': 1}]},
        'fields': [], 'attachments': [], 'passwordHistory': [], 'collectionIds': [],
        'revisionDate': '2026-10-21T12:00:00.000Z', 'creationDate': '2026-10-21T12:00:00.000Z',
        'deletedDate': None, 'object': 'cipherDetails', 'edit': True, 'viewPassword': True}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self, value, status=200):
            data = json.dumps(value).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            path = urlsplit(self.path).path; calls.append(('GET', path))
            if path == '/api/config':
                self.respond({'version': '2026.10.0', 'gitHash': 'fixture', 'object': 'config',
                    'environment': {'vault': origin, 'api': origin + '/api', 'identity': origin + '/identity',
                    'notifications': origin + '/notifications', 'icons': origin + '/icons'},
                    'featureStates': {}})
            elif path == '/api/accounts/revision-date':
                self.respond('2026-10-21T12:00:00.000Z')
            elif path == '/api/sync':
                self.respond({'object': 'sync', 'profile': {'id': uid, 'email': email, 'name': 'Fixture',
                    'key': bundle['key'], 'privateKey': bundle['keys']['encryptedPrivateKey'],
                    'accountKeys': account_keys,
                    'emailVerified': True, 'premium': False, 'organizations': [], 'providers': [],
                    'securityStamp': 'fixture-stamp', 'twoFactorEnabled': False,
                    'masterPasswordHint': None, 'culture': 'en-US', 'object': 'profile'},
                    'folders': [{'id': folder, 'name': bundle['folder'], 'object': 'folder'}],
                    'ciphers': [cipher], 'collections': [], 'domains': {'equivalentDomains': [],
                    'globalEquivalentDomains': [], 'object': 'domains'}, 'policies': [], 'sends': []})
            else:
                self.respond({}, 404)
        def do_POST(self):
            path = urlsplit(self.path).path; calls.append(('POST', path))
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if path.endswith('/accounts/prelogin') or path.endswith('/accounts/prelogin/password'):
                self.respond({'kdf': 0, 'kdfIterations': 600000, 'kdfMemory': None, 'kdfParallelism': None,
                    'salt': email, 'kdfSettings': {'kdfType': 0, 'iterations': 600000}})
            elif path == '/identity/connect/token':
                values = parse_qs(raw.decode())
                is_refresh = values.get('grant_type') == ['refresh_token'] and values.get('refresh_token') == ['fixture-refresh']
                if not is_refresh and values.get('password', [''])[0] != bundle['masterPasswordHash']:
                    return self.respond({'error': 'invalid_grant'}, 400)
                self.respond({'access_token': access, 'refresh_token': 'fixture-refresh',
                    'expires_in': 3600, 'token_type': 'Bearer', 'scope': 'api offline_access',
                    'Key': bundle['key'], 'PrivateKey': bundle['keys']['encryptedPrivateKey'],
                    'AccountKeys': account_keys,
                    'Kdf': 0, 'KdfIterations': 600000, 'ForcePasswordReset': False,
                    'UserDecryptionOptions': {'HasMasterPassword': True,
                        'MasterPasswordUnlock': {'Kdf': {'KdfType': 0, 'Iterations': 600000},
                        'MasterKeyWrappedUserKey': bundle['key'], 'MasterKeyEncryptedUserKey': bundle['key'], 'Salt': email}}})
            elif path == '/api/accounts/key-management/user-key-id':
                self.respond({})
            else:
                self.respond({}, 404)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    tls_directory = tempfile.TemporaryDirectory(prefix='pivault-cli-tls-')
    certificate = Path(tls_directory.name) / 'certificate.pem'
    private_key = Path(tls_directory.name) / 'private-key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', str(private_key), '-out', str(certificate), '-subj', '/CN=127.0.0.1',
        '-addext', 'subjectAltName=IP:127.0.0.1', '-days', '1'],
        capture_output=True, check=True, timeout=30)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, private_key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    origin = 'https://127.0.0.1:' + str(server.server_port)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix='pivault-cli-test-') as folder_name:
            env = dict({k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}, BITWARDENCLI_APPDATA_DIR=folder_name,
                       PIVAULT_FIXTURE_PASSWORD=password, BW_NOINTERACTION='true',
                       NODE_EXTRA_CA_CERTS=str(certificate))
            def cli(*args):
                value = subprocess.run(['node', str(cli_path), *args], env=env,
                    capture_output=True, text=True, timeout=90)
                if value.returncode:
                    raise RuntimeError('Official CLI check failed: ' + value.stderr[-2000:] + '\nRoutes: ' + repr(calls))
                return value.stdout.strip()
            cli('config', 'server', origin)
            session = cli('login', email, '--passwordenv', 'PIVAULT_FIXTURE_PASSWORD', '--raw')
            env['BW_SESSION'] = session
            cli('sync')
            decoded = json.loads(cli('list', 'items'))
            assert len(decoded) == 1 and decoded[0]['name'] == item['name']
            assert decoded[0]['login']['username'] == item['username']
            assert decoded[0]['login']['password'] == password
            assert decoded[0]['login']['uris'][0]['uri'] == item['uri']
            print('PASS: official Bitwarden CLI decrypts the browser-generated personal vault login.')
    finally:
        server.shutdown(); server.server_close()
        tls_directory.cleanup()


if __name__ == '__main__':
    verify(Path(sys.argv[1]))
