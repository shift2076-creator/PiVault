"""Tests for the contacts editor: vCard handling and the server against a fake Radicale."""
import base64
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape as xml_escape

import server
import vcard

USER, PASSWORD = 'snx', 'test-only-password'
AUTH = 'Basic ' + base64.b64encode(f'{USER}:{PASSWORD}'.encode()).decode()


class FakeRadicale(BaseHTTPRequestHandler):
    """Implements the handful of CardDAV calls the editor makes."""
    items = {}
    counter = 0

    def log_message(self, *args):
        pass

    def body(self):
        length = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(length).decode('utf-8') if length else ''

    def respond(self, status, body=b'', headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if self.headers.get('Authorization') == AUTH:
            return True
        self.respond(401)
        return False

    def name(self):
        return self.path.rsplit('/', 1)[-1]

    def do_REPORT(self):
        self.body()
        if not self.authorized():
            return
        parts = [f'<D:response><D:href>/{USER}/contacts/{name}</D:href><D:propstat><D:prop>'
                 f'<D:getetag>{etag}</D:getetag><C:address-data>{xml_escape(text)}</C:address-data>'
                 '</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>'
                 for name, (etag, text) in FakeRadicale.items.items()]
        xml = ('<?xml version="1.0"?><D:multistatus xmlns:D="DAV:" '
               'xmlns:C="urn:ietf:params:xml:ns:carddav">' + ''.join(parts) + '</D:multistatus>')
        self.respond(207, xml.encode(), {'Content-Type': 'application/xml'})

    def do_GET(self):
        if not self.authorized():
            return
        if self.name() not in FakeRadicale.items:
            return self.respond(404)
        etag, text = FakeRadicale.items[self.name()]
        self.respond(200, text.encode(), {'ETag': etag, 'Content-Type': 'text/vcard'})

    def do_PUT(self):
        text = self.body()
        if not self.authorized():
            return
        name, existed = self.name(), self.name() in FakeRadicale.items
        if self.headers.get('If-None-Match') == '*' and existed:
            return self.respond(412)
        if 'If-Match' in self.headers:
            if not existed or FakeRadicale.items[name][0] != self.headers['If-Match']:
                return self.respond(412)
        FakeRadicale.counter += 1
        etag = f'"e{FakeRadicale.counter}"'
        FakeRadicale.items[name] = (etag, text)
        self.respond(204 if existed else 201, b'', {'ETag': etag})

    def do_DELETE(self):
        if not self.authorized():
            return
        name = self.name()
        if 'If-Match' in self.headers and FakeRadicale.items.get(name, ('',))[0] != self.headers['If-Match']:
            return self.respond(412)
        if name not in FakeRadicale.items:
            return self.respond(404)
        del FakeRadicale.items[name]
        self.respond(204)


def start(httpd):
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return f'http://127.0.0.1:{httpd.server_address[1]}'


class VcardTests(unittest.TestCase):
    def test_round_trip_keeps_fields_and_escapes_values(self):
        contact = {'uid': 'abc', 'given': 'Ann', 'family': 'Lee; Jr', 'org': 'Acme, Inc',
                   'title': 'Engineer', 'url': 'https://example.com', 'birthday': '1980-02-03',
                   'note': 'Line one\nLine two; fine, really',
                   'emails': [{'type': 'work', 'value': 'ann@example.com'}],
                   'phones': [{'type': 'cell', 'value': '+1 555 0100'}],
                   'address': {'street': '1 Main St', 'city': 'Springfield', 'region': 'IL',
                               'postal': '62701', 'country': 'USA'}}
        parsed, extra = vcard.parse(vcard.serialize(contact, [], '20260101T000000Z'))
        self.assertEqual(extra, [])
        for key in ('given', 'family', 'org', 'title', 'url', 'birthday', 'note'):
            self.assertEqual(parsed[key], contact[key], key)
        self.assertEqual(parsed['emails'], contact['emails'])
        self.assertEqual(parsed['phones'], contact['phones'])
        self.assertEqual(parsed['address'], contact['address'])

    def test_unknown_properties_survive_a_round_trip(self):
        text = ('BEGIN:VCARD\r\nVERSION:3.0\r\nUID:abc\r\nFN:Ann Lee\r\nN:Lee;Ann;;;\r\n'
                'X-KEEP:yes\r\nX-LONG:' + 'a' * 60 + '\r\n ' + 'a' * 20 + '\r\nEND:VCARD\r\n')
        contact, extra = vcard.parse(text)
        self.assertEqual(contact['given'], 'Ann')
        self.assertIn('X-KEEP:yes', extra)
        rebuilt = vcard.serialize(contact, extra, '20260101T000000Z')
        self.assertIn('X-KEEP:yes', vcard.unfold(rebuilt))
        self.assertIn('X-LONG:' + 'a' * 80, vcard.unfold(rebuilt))

    def test_full_name_is_used_when_there_are_no_name_parts(self):
        contact, _ = vcard.parse('BEGIN:VCARD\r\nFN:Acme Supply\r\nEND:VCARD\r\n')
        text = vcard.serialize(contact, [], '20260101T000000Z')
        self.assertIn('FN:Acme Supply\r\n', text)

    def test_physical_lines_stay_within_75_octets(self):
        contact = {'note': 'x' * 300, 'emails': [{'type': '', 'value': 'é' * 60}]}
        for line in vcard.serialize(contact, [], '20260101T000000Z').split('\r\n'):
            self.assertLessEqual(len(line.encode('utf-8')), 75)


class ServerTests(unittest.TestCase):
    def setUp(self):
        FakeRadicale.items = {}
        radicale_url = start(ThreadingHTTPServer(('127.0.0.1', 0), FakeRadicale))
        self.httpd = server.make_server('127.0.0.1', 0, radicale_url)
        self.base = start(self.httpd)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.httpd.server_close)

    def request(self, method, path, payload=None, headers=None, auth=AUTH):
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        request_headers = dict(headers or {})
        if auth:
            request_headers['Authorization'] = auth
        if payload is not None:
            request_headers['Content-Type'] = 'application/json'
        request = Request(self.base + path, data=data, method=method, headers=request_headers)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def contact_payload(self, **overrides):
        payload = {'given': 'Ann', 'family': 'Lee', 'org': 'Acme', 'title': '',
                   'url': '', 'birthday': '', 'note': 'First note',
                   'emails': [{'type': 'work', 'value': 'ann@example.com'}],
                   'phones': [], 'address': {'street': '', 'city': '', 'region': '',
                                              'postal': '', 'country': ''}}
        payload.update(overrides)
        return payload

    def test_requests_without_sign_in_are_challenged(self):
        status, headers, _ = self.request('GET', '/', auth=None)
        self.assertEqual(status, 401)
        self.assertIn('Basic realm=', headers['WWW-Authenticate'])

    def test_wrong_password_is_rejected_by_radicale(self):
        bad = 'Basic ' + base64.b64encode(f'{USER}:wrong'.encode()).decode()
        status, headers, _ = self.request('GET', '/api/contacts', auth=bad)
        self.assertEqual(status, 401)
        self.assertIn('Basic realm=', headers['WWW-Authenticate'])

    def test_contact_lifecycle_create_edit_delete(self):
        status, _, body = self.request('POST', '/api/contacts', self.contact_payload())
        self.assertEqual(status, 201)
        created = json.loads(body)
        contact_id, etag = created['id'], created['etag']

        status, _, body = self.request('GET', '/api/contacts')
        listed = json.loads(body)
        self.assertEqual([(c['id'], c['given'], c['family']) for c in listed],
                         [(contact_id, 'Ann', 'Lee')])

        stale = self.contact_payload(note='Edited', etag='"stale"')
        status, _, _ = self.request('PUT', f'/api/contacts/{contact_id}', stale)
        self.assertEqual(status, 409)

        edit = self.contact_payload(note='Edited', etag=etag)
        status, _, body = self.request('PUT', f'/api/contacts/{contact_id}', edit)
        self.assertEqual(status, 200)
        self.assertNotEqual(json.loads(body)['etag'], etag)
        _, _, body = self.request('GET', '/api/contacts')
        self.assertEqual(json.loads(body)[0]['note'], 'Edited')

        status, _, _ = self.request('DELETE', f'/api/contacts/{contact_id}')
        self.assertEqual(status, 400)
        current = json.loads(self.request('GET', '/api/contacts')[2])[0]['etag']
        status, _, _ = self.request('DELETE', f'/api/contacts/{contact_id}',
                                    headers={'If-Match': current})
        self.assertEqual(status, 204)
        _, _, body = self.request('GET', '/api/contacts')
        self.assertEqual(json.loads(body), [])

    def test_editing_keeps_properties_the_editor_does_not_show(self):
        FakeRadicale.items['abc123.vcf'] = ('"e0"', 'BEGIN:VCARD\r\nVERSION:3.0\r\nUID:abc123\r\n'
                                                    'FN:Ann Lee\r\nN:Lee;Ann;;;\r\nX-KEEP:yes\r\n'
                                                    'END:VCARD\r\n')
        _, _, body = self.request('GET', '/api/contacts')
        etag = json.loads(body)[0]['etag']
        status, _, _ = self.request('PUT', '/api/contacts/abc123', self.contact_payload(etag=etag))
        self.assertEqual(status, 200)
        self.assertIn('X-KEEP:yes', vcard.unfold(FakeRadicale.items['abc123.vcf'][1]))

    def test_invalid_birthday_is_rejected(self):
        status, _, _ = self.request('POST', '/api/contacts',
                                    self.contact_payload(birthday='next tuesday'))
        self.assertEqual(status, 400)

    def test_page_and_script_are_served_behind_sign_in(self):
        status, _, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'<title>Contacts</title>', body)
        status, _, _ = self.request('GET', '/static/app.js')
        self.assertEqual(status, 200)


if __name__ == '__main__':
    unittest.main()
