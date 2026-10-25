"""Unified launcher search: one server-side request per ticked app.

Each connector calls its app's own search API on the private Docker network with a
read-only search credential from the installer-owned native.json. A connector only
returns what that credential can see, so configure each one with content every user
who has that app already shares (see docs/SEARCH.md). Results are plain data; the
template escapes every field.
"""
import base64
import hashlib
import html
import json
import re
import secrets
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from .integrations.native_http import NoRedirect

LIMIT = 10
TIMEOUT = 6
MAX_BYTES = 2 * 1024 * 1024
CONNECTORS = {}
TAGS = re.compile(r'<[^>]*>')
ATOM = '{http://www.w3.org/2005/Atom}'


class SearchError(Exception):
    """An app could not be searched. Never carries upstream bodies or secrets."""


@dataclass
class Result:
    title: str
    url: str
    detail: str = ''
    snippet: str = ''


def _origin(value, name):
    parsed = urlsplit(value or '')
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError(name + ' needs an http(s) address without credentials or query.')
    return value.rstrip('/')


def _text(value):
    return ' '.join(html.unescape(TAGS.sub('', value or '')).split())


class Connector:
    """Shared HTTP plumbing: no proxies, no redirects, bounded size and time."""
    modes = ('shared', 'isolated')

    def __init__(self, origin, link, headers=None):
        self.origin = _origin(origin, self.key)
        self.link = _origin(link, self.key)
        self.headers = dict(headers or {})
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def allowed(self, user):
        from .services import ADAPTERS
        # Apps without per-user portal accounts (Kiwix, Calibre-Web, Paperless, Grocy) are
        # shared with every signed-in user; provisioned apps follow the user's access.
        if self.key not in ADAPTERS:
            return True
        return user.service_access.get(self.key, 'none') in self.modes

    def fetch(self, path, query=()):
        url = self.origin + path + ('?' + urlencode(query, doseq=True) if query else '')
        try:
            with self.opener.open(Request(url, headers=self.headers), timeout=TIMEOUT) as response:
                raw = response.read(MAX_BYTES + 1)
        except (HTTPError, URLError, OSError, ValueError):
            raise SearchError(self.key) from None
        if len(raw) > MAX_BYTES:
            raise SearchError(self.key)
        return raw

    def json(self, path, query=()):
        try:
            return json.loads(self.fetch(path, query))
        except ValueError:
            raise SearchError(self.key) from None

    def xml(self, path, query=()):
        try:
            return ET.fromstring(self.fetch(path, query))
        except ET.ParseError:
            raise SearchError(self.key) from None

    def url(self, path):
        # Upstream paths become same-app links only; never another host.
        if not path.startswith('/') or path.startswith('//'):
            raise SearchError(self.key)
        return self.link + path


class WikiSearch(Connector):
    """kiwix-serve full-text search by book UUID; one request per ZIM language (a kiwix rule)."""
    key = 'wiki'

    def search(self, term):
        languages = {}
        for entry in self.xml('/catalog/v2/entries', {'count': -1}).iter(ATOM + 'entry'):
            # The catalog <name> is ZIM metadata, not the server's URL name, so
            # books.name rejects it ("No such book"); the UUID always matches.
            book = (entry.findtext(ATOM + 'id') or '').removeprefix('urn:uuid:')
            if book:
                languages.setdefault(entry.findtext(ATOM + 'language') or '', []).append(book)
        results = []
        for books in languages.values():
            feed = self.xml('/search', {'pattern': term, 'books.id': books,
                                        'format': 'xml', 'pageLength': LIMIT})
            for item in feed.iter('item'):
                link = item.findtext('link') or ''
                description = item.find('description')
                results.append(Result(
                    item.findtext('title') or link, self.url(link),
                    item.findtext('book/title') or '',
                    _text(''.join(description.itertext())) if description is not None else ''))
        return results[:LIMIT]


class VideoSearch(Connector):
    """Jellyfin /Search/Hints; without userId an API key searches every library."""
    key = 'videos'

    def __init__(self, origin, link, api_key):
        if not api_key or any(c in api_key for c in '\r\n'):
            raise ValueError('Videos search needs a Jellyfin API key.')
        super().__init__(origin, link, {'X-Emby-Token': api_key})

    def search(self, term):
        value = self.json('/Search/Hints', {
            'searchTerm': term, 'limit': LIMIT, 'includePeople': 'false', 'includeGenres': 'false',
            'includeStudios': 'false', 'includeArtists': 'false'})
        results = []
        for hint in value.get('SearchHints', []) if isinstance(value, dict) else []:
            item = str(hint.get('Id') or hint.get('ItemId') or '')
            if not item:
                continue
            detail = ' · '.join(str(x) for x in (hint.get('Type'), hint.get('Series'),
                                                 hint.get('ProductionYear')) if x)
            results.append(Result(str(hint.get('Name') or item),
                                  self.link + '/web/#/details?id=' + quote(item, safe=''), detail))
        return results


class MusicSearch(Connector):
    """Navidrome Subsonic search3, limited to the configured shared libraries."""
    key = 'music'

    def __init__(self, origin, link, username, password, library_ids=None):
        if not username or not password:
            raise ValueError('Music search needs a Navidrome account.')
        super().__init__(origin, link)
        self.username, self.password, self.library_ids = username, password, library_ids or []

    def search(self, term):
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.password + salt).encode(), usedforsecurity=False).hexdigest()
        query = {'u': self.username, 't': token, 's': salt, 'v': '1.16.1', 'c': 'pivault-search',
                 'f': 'json', 'query': term, 'artistCount': 3, 'albumCount': 4, 'songCount': LIMIT}
        if self.library_ids:
            query['musicFolderId'] = list(self.library_ids)
        body = self.json('/rest/search3', query).get('subsonic-response', {})
        if body.get('status') != 'ok':
            raise SearchError(self.key)
        found = body.get('searchResult3', {})
        page = lambda kind, value: self.link + '/app/#/' + kind + '/' + quote(str(value), safe='') + '/show'
        results = [Result(a.get('name', ''), page('artist', a['id']), 'Artist')
                   for a in found.get('artist', []) if a.get('id')]
        results += [Result(a.get('name', ''), page('album', a['id']), 'Album · ' + a.get('artist', ''))
                    for a in found.get('album', []) if a.get('id')]
        results += [Result(s.get('title', ''), page('album', s['albumId']),
                           'Song · ' + ' · '.join(x for x in (s.get('artist'), s.get('album')) if x))
                    for s in found.get('song', []) if s.get('albumId')]
        return results[:LIMIT]


class BookSearch(Connector):
    """Calibre-Web OPDS search with a dedicated read-only account."""
    key = 'books'
    COVER = re.compile(r'/opds/cover/(\d+)')

    def __init__(self, origin, link, username, password):
        if not username or not password:
            raise ValueError('Books search needs a Calibre-Web account.')
        raw = base64.b64encode((username + ':' + password).encode()).decode()
        super().__init__(origin, link, {'Authorization': 'Basic ' + raw})

    def search(self, term):
        results = []
        for entry in self.xml('/opds/search', {'query': term}).iter(ATOM + 'entry'):
            book = next((m.group(1) for link in entry.iter(ATOM + 'link')
                         for m in [self.COVER.search(link.get('href', ''))] if m), None)
            if not book:
                continue
            authors = ', '.join(a.findtext(ATOM + 'name') or '' for a in entry.findall(ATOM + 'author'))
            results.append(Result(entry.findtext(ATOM + 'title') or '', self.link + '/book/' + book, authors))
        return results[:LIMIT]


class DocumentSearch(Connector):
    """Paperless-ngx full-text search; results follow the token owner's permissions."""
    key = 'documents'

    def __init__(self, origin, link, token):
        if not token or any(c in token for c in '\r\n'):
            raise ValueError('Documents search needs a Paperless-ngx token.')
        super().__init__(origin, link, {'Authorization': 'Token ' + token, 'Accept': 'application/json'})

    def search(self, term):
        value = self.json('/api/documents/', {'query': term, 'page_size': LIMIT})
        return [Result(d.get('title', ''), self.link + '/documents/' + str(int(d['id'])) + '/details', '',
                       _text((d.get('__search_hit__') or {}).get('highlights', '')))
                for d in value.get('results', []) if isinstance(d.get('id'), int)]


class InventorySearch(Connector):
    """Homebox entity search with an API key from the shared household."""
    key = 'inventory'
    modes = ('shared',)  # An isolated household is not visible to the shared key.

    def __init__(self, origin, link, api_key):
        if not api_key or any(c in api_key for c in '\r\n'):
            raise ValueError('Inventory search needs a Homebox API key.')
        super().__init__(origin, link, {'Authorization': 'Bearer ' + api_key.removeprefix('Bearer ')})

    def search(self, term):
        value = self.json('/api/v1/entities', {'q': term, 'pageSize': LIMIT, 'page': 1})
        return [Result(i.get('name', ''), self.link + '/item/' + quote(str(i['id']), safe=''),
                       (i.get('parent') or {}).get('name', ''), _text(i.get('description', '')))
                for i in value.get('items', []) if i.get('id')]


class PantrySearch(Connector):
    """Grocy product lookup; Grocy keeps one shared household."""
    key = 'pantry'
    UNSAFE = re.compile(r'[^\w .\-]', re.UNICODE)  # Grocy's filter grammar rejects other characters.

    def __init__(self, origin, link, api_key):
        if not api_key or any(c in api_key for c in '\r\n'):
            raise ValueError('Pantry search needs a Grocy API key.')
        super().__init__(origin, link, {'GROCY-API-KEY': api_key, 'Accept': 'application/json'})

    def search(self, term):
        safe = self.UNSAFE.sub(' ', term).strip()
        if not safe:
            return []
        value = self.json('/api/objects/products', {'query[]': 'name~' + safe, 'limit': LIMIT})
        return [Result(p.get('name', ''), self.link + '/product/' + str(int(p['id'])), '',
                       _text(p.get('description', '')))
                for p in value if isinstance(p, dict) and str(p.get('id', '')).isdigit()][:LIMIT]


def configure(config):
    """Build connectors from native.json's optional 'search' section."""
    CONNECTORS.clear()
    search = config.get('search', {})
    jellyfin, navidrome = config.get('jellyfin', {}), config.get('navidrome', {})
    builders = {
        'wiki': lambda c: WikiSearch(c['origin'], c['link']),
        'videos': lambda c: VideoSearch(c.get('origin') or jellyfin.get('origin'), c['link'],
                                        c.get('api_key') or jellyfin.get('api_key')),
        'music': lambda c: MusicSearch(c.get('origin') or navidrome.get('origin'), c['link'],
                                       c.get('username') or navidrome.get('admin_username'),
                                       c.get('password') or navidrome.get('admin_password'),
                                       c.get('library_ids', navidrome.get('library_ids'))),
        'books': lambda c: BookSearch(c['origin'], c['link'], c['username'], c['password']),
        'documents': lambda c: DocumentSearch(c['origin'], c['link'], c['token']),
        'inventory': lambda c: InventorySearch(c['origin'], c['link'], c['api_key']),
        'pantry': lambda c: PantrySearch(c['origin'], c['link'], c['api_key']),
    }
    for key, value in search.items():
        if key not in builders:
            raise ValueError('Unknown search app: ' + key)
        CONNECTORS[key] = builders[key](value)


def available(user):
    from .services import SERVICES
    return [(key, label) for key, label in SERVICES
            if key in CONNECTORS and CONNECTORS[key].allowed(user)]


def run(user, term, keys):
    """Query only the requested apps the user may use, in parallel, in launcher order."""
    apps = [(key, label) for key, label in available(user) if key in keys]
    sections = {key: {'key': key, 'label': label, 'results': [], 'error': True} for key, label in apps}
    if not apps:
        return []
    pool = ThreadPoolExecutor(max_workers=len(apps))
    futures = {pool.submit(CONNECTORS[key].search, term): key for key, _ in apps}
    done, _ = wait(futures, timeout=TIMEOUT * 2)
    # A slow app must not hold the page; its section reports it as unavailable.
    pool.shutdown(wait=False, cancel_futures=True)
    for future in done:
        try:
            sections[futures[future]].update(results=future.result(), error=False)
        except Exception:
            pass  # Nothing from the upstream error is shown.
    return [sections[key] for key, _ in apps]
