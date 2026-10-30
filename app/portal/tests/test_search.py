import json
from unittest.mock import patch
from django.test import TestCase
from portal import search
from portal.models import User

CONFIG = {
    'jellyfin': {'origin': 'http://jellyfin:8096', 'api_key': 'jf-key'},
    'navidrome': {'origin': 'http://navidrome:4533', 'admin_username': 'nd', 'admin_password': 'pw',
                  'library_ids': [1]},
    'search': {
        'wiki': {'origin': 'http://kiwix:8080', 'link': 'http://192.168.4.90:8084'},
        'videos': {'link': 'http://192.168.4.90:8096'},
        'music': {'link': 'http://192.168.4.90:4533/music'},
        'books': {'origin': 'http://calibre-web:8083', 'link': 'http://192.168.4.90:8083',
                  'username': 'search', 'password': 'pw'},
        'documents': {'origin': 'http://paperless:8000', 'link': 'http://192.168.4.90:8085', 'token': 't'},
        'inventory': {'origin': 'http://homebox:7745', 'link': 'http://192.168.4.90:8087', 'api_key': 'k'},
        'pantry': {'origin': 'http://grocy:80', 'link': 'http://192.168.4.90:9283', 'api_key': 'g'},
    },
}
CATALOG = b'''<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>urn:uuid:aaaa</id><name>wikipedia_en</name><language>eng</language></entry>
<entry><id>urn:uuid:bbbb</id><name>wikivoyage_en</name><language>eng</language></entry>
<entry><id>urn:uuid:cccc</id><name>wikipedia_fr</name><language>fra</language></entry>
<entry><id>urn:uuid:dddd</id><name>gutenberg_en</name><title>Project Gutenberg</title><language>eng</language>
<link type="text/html" href="/content/gutenberg_en_all_2025-11"/>
<tags>_category:gutenberg;_ftindex:no</tags></entry></feed>'''
KIWIX = b'''<rss version="2.0"><channel><item><title>Apollo 11</title>
<link>/content/wikipedia_en/A/Apollo_11</link><description>The <b>Apollo</b> mission...</description>
<book><title>Wikipedia</title></book></item></channel></rss>'''
OPDS = b'''<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Moon Book</title><id>urn:uuid:0d0d</id><author><name>A. Writer</name></author>
<link type="image/jpeg" href="/opds/cover/42" rel="http://opds-spec.org/image"/>
<link type="image/jpeg" href="/opds/cover/42" rel="http://opds-spec.org/image/thumbnail"/>
<link rel="http://opds-spec.org/acquisition" href="/opds/download/42/epub/" title="EPUB" type="application/epub+zip"/>
</entry>
<entry><title>Moon Atlas</title><id>urn:uuid:0e0e</id><author><name>B. Maker</name></author>
<link rel="http://opds-spec.org/acquisition" href="/opds/download/43/pdf/" title="PDF" type="application/pdf"/>
</entry></feed>'''


def responses(calls):
    def fetch(connector, path, query=()):
        calls.append((connector.key, path, dict(query)))
        if path == '/catalog/v2/entries':
            return CATALOG
        if path == '/search':
            return KIWIX
        if path == '/opds/search':
            return OPDS
        body = {
            '/Search/Hints': {'SearchHints': [{'Id': 'abc', 'Name': '<b>Apollo</b>', 'Type': 'Movie',
                                               'ProductionYear': 1995}]},
            '/rest/search3': {'subsonic-response': {'status': 'ok', 'searchResult3': {
                'album': [{'id': 'al1', 'name': 'Moon', 'artist': 'Band'}],
                'song': [{'id': 's1', 'title': 'Apollo', 'albumId': 'al1', 'artist': 'Band'}]}}},
            '/api/documents/': {'results': [{'id': 7, 'title': 'Receipt',
                                             '__search_hit__': {'highlights': 'a <span class="match">moon</span>'}}]},
            '/api/v1/entities': {'items': [{'id': 'u-1', 'name': 'Telescope', 'parent': {'name': 'Garage'}}]},
            '/api/objects/products': [{'id': 3, 'name': 'Moon pie'}],
            '/suggest': [{'kind': 'path', 'value': 'Alice in Wonderland', 'label': 'Alice', 'path': 'Alice in Wonderland.11'}],
        }[path]
        return json.dumps(body).encode()
    return fetch


class SearchTests(TestCase):
    def setUp(self):
        search.configure(CONFIG)
        self.addCleanup(search.CONNECTORS.clear)
        everything = {key: 'shared' for key in ('books', 'music', 'videos', 'documents', 'wiki',
                                                'inventory', 'pantry', 'games', 'passwords')}
        self.user = User.objects.create_user(username='u', email='u@example.com', display_name='U',
                                             role='user', service_access=everything)
        self.calls = []
        patcher = patch.object(search.Connector, 'fetch', responses(self.calls))
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, url):
        self.client.force_login(self.user)
        return self.client.get(url, secure=True)

    def test_signed_out_visitors_get_nothing(self):
        self.assertEqual(self.client.get('/search/apps', secure=True).status_code, 401)
        response = self.client.get('/search?q=apollo', secure=True)
        self.assertRedirects(response, '/users/sign-in', fetch_redirect_response=False)
        self.assertEqual(self.calls, [])

    def test_provisioned_apps_follow_access_and_unprovisioned_apps_are_shared(self):
        self.user.service_access = {'inventory': 'isolated', 'music': 'none', 'videos': 'shared'}
        self.user.save()
        provisioned = {key: object() for key in ('inventory', 'music', 'videos')}
        with patch.dict('portal.services.ADAPTERS', provisioned, clear=True):
            keys = [app['key'] for app in self.get('/search/apps').json()['apps']]
        # Isolated Inventory and no-access Music are hidden; Wiki, Books, Documents and
        # Pantry have no portal accounts, so every signed-in user shares them.
        self.assertEqual(keys, ['books', 'videos', 'documents', 'wiki', 'pantry'])

    def test_only_ticked_apps_are_called(self):
        response = self.get('/search?q=apollo&app=wiki&app=videos&app=passwords')
        self.assertEqual({key for key, _, _ in self.calls}, {'wiki', 'videos'})
        self.assertContains(response, 'href="http://192.168.4.90:8084/content/wikipedia_en/A/Apollo_11"')
        self.assertContains(response, 'href="http://192.168.4.90:8096/web/#/details?id=abc"')
        self.assertContains(response, '&lt;b&gt;Apollo&lt;/b&gt;')  # Upstream text is escaped.
        self.assertNotContains(response, 'Moon pie')

    def test_books_rank_author_matches_before_title_matches(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Poetical Essays</title><author><name>Anna Writer</name></author><link href="/opds/cover/1"/></entry>
<entry><title>Short Fiction</title><author><name>Edgar Allan Poe</name></author><link href="/opds/cover/2"/></entry>
</feed>'''
        with patch.object(search.Connector, 'fetch', lambda connector, path, query=(): feed):
            results = search.CONNECTORS['books'].search('Poe')
        self.assertEqual([r.title for r in results], ['Short Fiction', 'Poetical Essays'])
        self.assertEqual(results[0].url, 'http://192.168.4.90:8083/book/2')

    def test_wiki_suggests_titles_for_zims_without_a_full_text_index(self):
        results = search.CONNECTORS['wiki'].search('alice')
        suggest = [query for _, path, query in self.calls if path == '/suggest']
        self.assertEqual(suggest, [{'content': 'gutenberg_en_all_2025-11', 'term': 'alice', 'count': search.LIMIT}])
        self.assertIn('http://192.168.4.90:8084/content/gutenberg_en_all_2025-11/Alice%20in%20Wonderland.11',
                      [result.url for result in results])
        self.assertFalse(any(path == '/search' and 'gutenberg' in str(query) for _, path, query in self.calls))

    def test_wiki_ranks_title_matches_above_full_text_hits(self):
        items = ''.join('<item><title>Hit %d</title><link>/content/wikipedia_en/A/Hit_%d</link></item>' % (i, i)
                        for i in range(30))
        feed = ('<rss version="2.0"><channel>' + items + '</channel></rss>').encode()
        base = responses(self.calls)

        def fetch(connector, path, query=()):
            return feed if path == '/search' else base(connector, path, query)

        with patch.object(search.Connector, 'fetch', fetch):
            results = search.CONNECTORS['wiki'].search('alice')
        self.assertEqual(len(results), search.LIMIT)
        self.assertEqual(results[0].title, 'Alice in Wonderland')
        self.assertEqual(results[0].detail, 'Project Gutenberg')

    def test_wiki_searches_once_per_language(self):
        self.get('/search?q=apollo&app=wiki')
        books = [query['books.id'] for key, path, query in self.calls if path == '/search']
        # gutenberg_en has no full-text index, so it is left out of the English batch.
        self.assertEqual(sorted(books), [['aaaa', 'bbbb'], ['cccc']])
        self.assertFalse(any('books.name' in query for _, _, query in self.calls))

    def test_every_app_returns_links_into_itself(self):
        response = self.get('/search?q=moon')
        for link in ('http://192.168.4.90:4533/music/app/#/album/al1/show', 'http://192.168.4.90:8083/book/42',
                     'http://192.168.4.90:8083/book/43',
                     'http://192.168.4.90:8085/documents/7/details', 'http://192.168.4.90:8087/item/u-1',
                     'http://192.168.4.90:9283/product/3'):
            self.assertContains(response, 'href="' + link + '"')
        music = next(query for key, _, query in self.calls if key == 'music')
        self.assertEqual(music['musicFolderId'], [1])
        self.assertNotIn('p', music)  # Token auth only; the password never travels.

    def test_a_failing_app_is_reported_without_upstream_details(self):
        def broken(connector, path, query=()):
            raise search.SearchError(connector.key)
        with patch.object(search.Connector, 'fetch', broken):
            response = self.get('/search?q=apollo&app=documents')
        self.assertContains(response, 'Documents could not be searched right now.')

    def test_pantry_strips_characters_outside_grocys_filter_grammar(self):
        self.get('/search?q=moon%27s+pie%26&app=pantry')
        query = next(query for key, _, query in self.calls if key == 'pantry')
        self.assertEqual(query['query[]'], 'name~moon s pie')

    def test_wiki_rejects_links_to_other_hosts(self):
        connector = search.CONNECTORS['wiki']
        with self.assertRaises(search.SearchError):
            connector.url('//evil.example/x')

    def test_unknown_search_app_and_credentials_in_origins_are_rejected(self):
        with self.assertRaises(ValueError):
            search.configure({'search': {'passwords': {}}})
        with self.assertRaises(ValueError):
            search.configure({'search': {'wiki': {'origin': 'http://a:b@kiwix', 'link': 'http://x'}}})
