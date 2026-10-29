"""PiVault Contacts: a web editor for the signed-in user's Radicale address book.

The browser's Basic-auth credentials are passed to Radicale unchanged, so Radicale
remains the only place that checks passwords and the only place contacts are stored.
Nothing is written to disk by this server.
"""
import base64
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import vcard

STATIC = Path(__file__).resolve().parent / 'static'
STATIC_FILES = {'app.js': 'application/javascript; charset=utf-8',
                'app.css': 'text/css; charset=utf-8'}
REALM = 'PiVault Contacts'
ID_PATTERN = re.compile(r'^[A-Za-z0-9-]{1,64}$')
USER_PATTERN = re.compile(r'^[A-Za-z0-9._@-]{1,64}$')
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')
MAX_BODY = 256 * 1024
TEXT_LIMIT = 500
LIST_LIMIT = 20
LIST_BODY = b'''<?xml version="1.0" encoding="utf-8"?>
<C:addressbook-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">
  <D:prop><D:getetag/><C:address-data/></D:prop>
</C:addressbook-query>'''
NS = {'D': 'DAV:', 'C': 'urn:ietf:params:xml:ns:carddav'}
MESSAGES = {400: 'Invalid request.',
            401: 'Sign in with your PiVault contacts account.',
            403: 'This address book is not available to this account.',
            404: 'Contact not found.',
            409: 'This contact changed elsewhere. Reload it and try again.',
            502: 'The contacts service could not be reached.'}


class UpstreamError(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def parse_credentials(header):
    """Return (username, header) for a Basic Authorization header, or None."""
    if not header or not header.startswith('Basic '):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        return None
    user, separator, _ = decoded.partition(':')
    if not separator or not USER_PATTERN.match(user):
        return None
    return user, header


def radicale(base, method, path, auth, body=None, headers=None):
    request_headers = {'Authorization': auth}
    request_headers.update(headers or {})
    request = Request(base.rstrip('/') + path, data=body, method=method, headers=request_headers)
    try:
        with urlopen(request, timeout=15) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        return error.code, error.headers, error.read()
    except (URLError, OSError):
        raise UpstreamError(502)


def check(status, allowed):
    if status in allowed:
        return
    if status == 412:
        raise UpstreamError(409)
    if status in (401, 403, 404):
        raise UpstreamError(status)
    raise UpstreamError(502)


def collection(user):
    return f'/{quote(user)}/contacts/'


def list_contacts(base, user, auth):
    status, _, body = radicale(base, 'REPORT', collection(user), auth, LIST_BODY,
                               {'Depth': '1', 'Content-Type': 'application/xml; charset=utf-8'})
    if status == 404:
        return []
    check(status, (207,))
    items = []
    for response in ElementTree.fromstring(body).findall('D:response', NS):
        href = unquote(response.findtext('D:href', '', NS))
        data = response.findtext('.//C:address-data', '', NS)
        stem = href.rstrip('/').rsplit('/', 1)[-1]
        if not data or not stem.endswith('.vcf') or not ID_PATTERN.match(stem[:-4]):
            continue
        contact, _ = vcard.parse(data)
        contact['id'] = stem[:-4]
        contact['etag'] = response.findtext('.//D:getetag', '', NS)
        items.append(contact)
    items.sort(key=lambda item: ((item['family'] or item['full_name']).lower(),
                                 item['given'].lower()))
    return items


def fetch_contact(base, user, auth, contact_id):
    status, headers, body = radicale(base, 'GET', collection(user) + contact_id + '.vcf', auth)
    check(status, (200,))
    text = body.decode('utf-8')
    contact, extra = vcard.parse(text)
    return contact, extra, headers.get('ETag', '')


def sanitize(payload):
    if not isinstance(payload, dict):
        raise ValueError('Invalid request.')

    def text(value, limit=TEXT_LIMIT):
        return str(value or '').replace('\r', '').strip()[:limit]

    def entries(key):
        result = []
        for item in (payload.get(key) or [])[:LIST_LIMIT]:
            if not isinstance(item, dict):
                continue
            value = text(item.get('value'))
            if value:
                kind = vcard.TYPE_TOKEN.sub('', text(item.get('type'), 30))
                result.append({'type': kind, 'value': value})
        return result

    birthday = text(payload.get('birthday'), 10)
    if birthday and not DATE_PATTERN.match(birthday):
        raise ValueError('Birthday must be a date.')
    address = payload.get('address') or {}
    if not isinstance(address, dict):
        raise ValueError('Invalid request.')
    return {
        'given': text(payload.get('given')),
        'family': text(payload.get('family')),
        'org': text(payload.get('org')),
        'title': text(payload.get('title')),
        'url': text(payload.get('url')),
        'birthday': birthday,
        'note': text(payload.get('note'), 5000),
        'emails': entries('emails'),
        'phones': entries('phones'),
        'address': {field: text(address.get(field)) for field in vcard.ADDRESS_FIELDS},
    }


def save_contact(base, user, auth, payload, contact_id=None):
    fields = sanitize(payload)
    rev = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = collection(user)
    if contact_id is None:
        contact_id = uuid.uuid4().hex
        extra, headers = [], {'If-None-Match': '*'}
        contact = dict(fields)
    else:
        etag = payload.get('etag') or ''
        if not etag:
            raise ValueError('A version is required to save changes.')
        current, extra, _ = fetch_contact(base, user, auth, contact_id)
        contact = dict(current, **fields)
        headers = {'If-Match': etag}
    contact['uid'] = contact_id
    text = vcard.serialize(contact, extra, rev)
    status, response_headers, _ = radicale(
        base, 'PUT', path + contact_id + '.vcf', auth, text.encode('utf-8'),
        dict(headers, **{'Content-Type': 'text/vcard; charset=utf-8'}))
    check(status, (200, 201, 204))
    return contact_id, response_headers.get('ETag', '')


def delete_contact(base, user, auth, contact_id, etag):
    if not etag:
        raise ValueError('A version is required to delete a contact.')
    status, _, _ = radicale(base, 'DELETE', collection(user) + contact_id + '.vcf', auth,
                            None, {'If-Match': etag})
    check(status, (200, 204))


class Handler(BaseHTTPRequestHandler):
    server_version = 'PiVaultContacts/1.0'

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def do_PUT(self):
        self.dispatch('PUT')

    def do_DELETE(self):
        self.dispatch('DELETE')

    def dispatch(self, method):
        try:
            self.route(method)
        except ValueError as error:
            self.send_json(400, {'error': str(error) or MESSAGES[400]})
        except UpstreamError as error:
            if error.status == 401:
                self.send_auth_required()
            else:
                self.send_json(error.status, {'error': MESSAGES.get(error.status, MESSAGES[502])})

    def route(self, method):
        path = self.path.split('?', 1)[0]
        credentials = parse_credentials(self.headers.get('Authorization'))
        if credentials is None:
            return self.send_auth_required()
        user, auth = credentials
        base = self.server.radicale_url
        if method == 'GET' and path in ('/', '/index.html'):
            return self.send_file(STATIC / 'index.html', 'text/html; charset=utf-8')
        if method == 'GET' and path.startswith('/static/') and path[8:] in STATIC_FILES:
            return self.send_file(STATIC / path[8:], STATIC_FILES[path[8:]])
        if path == '/api/contacts':
            if method == 'GET':
                return self.send_json(200, list_contacts(base, user, auth))
            if method == 'POST':
                contact_id, etag = save_contact(base, user, auth, self.read_json())
                return self.send_json(201, {'id': contact_id, 'etag': etag})
        match = re.fullmatch(r'/api/contacts/([^/]+)', path)
        if match and ID_PATTERN.match(match.group(1)):
            contact_id = match.group(1)
            if method == 'PUT':
                saved, etag = save_contact(base, user, auth, self.read_json(), contact_id)
                return self.send_json(200, {'id': saved, 'etag': etag})
            if method == 'DELETE':
                delete_contact(base, user, auth, contact_id, self.headers.get('If-Match', ''))
                return self.send_empty(204)
        self.send_json(404, {'error': MESSAGES[404]})

    def read_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError(MESSAGES[400])
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError(MESSAGES[400])

    def send_auth_required(self):
        body = MESSAGES[401].encode('utf-8')
        self.send_response(401)
        self.send_header('WWW-Authenticate', f'Basic realm="{REALM}"')
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_empty(self, status):
        self.send_response(status)
        self.send_header('Content-Length', '0')
        self.end_headers()


def make_server(bind, port, radicale_url):
    httpd = ThreadingHTTPServer((bind, port), Handler)
    httpd.radicale_url = radicale_url
    return httpd


def main():
    bind = os.environ.get('PIVAULT_CONTACTS_BIND', '192.168.4.90')
    port = int(os.environ.get('PIVAULT_CONTACTS_PORT', '8089'))
    radicale_url = os.environ.get('PIVAULT_CONTACTS_RADICALE', 'http://192.168.4.90:5232')
    print(f'PiVault Contacts on http://{bind}:{port}/ using {radicale_url}', file=sys.stderr)
    make_server(bind, port, radicale_url).serve_forever()


if __name__ == '__main__':
    main()
