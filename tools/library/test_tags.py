import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('tags', ROOT / 'tags.py')
tags = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tags)


def epub(path, subjects=(), se_subjects=()):
    opf = ('<package xmlns="http://www.idpf.org/2007/opf"><metadata '
           'xmlns:dc="http://purl.org/dc/elements/1.1/">'
           + ''.join(f'<dc:subject>{s}</dc:subject>' for s in subjects)
           + ''.join(f'<meta property="se:subject">{s}</meta>' for s in se_subjects)
           + '</metadata></package>')
    with zipfile.ZipFile(path, 'w') as book:
        book.writestr('mimetype', 'application/epub+zip')
        book.writestr('META-INF/container.xml',
                      '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                      '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        book.writestr('OEBPS/content.opf', opf)
    return str(path)


class MapTests(unittest.TestCase):
    def test_vocabulary_tags_are_kept_case_insensitively(self):
        self.assertEqual(tags.normalise(['PiVault Trades', 'farming', 'Children’s']),
                         ["Children's", 'Farming', 'PiVault Trades'])

    def test_comma_split_lcsh_fragments_collapse_to_one_subject(self):
        # Calibre-Web splits "Chekhov, Anton Pavlovich, 1860-1904 -- Translations into English".
        self.assertEqual(tags.normalise(['Chekhov', 'Anton Pavlovich',
                                         '1860-1904 -- Translations into English', 'Short stories']),
                         ['Fiction', 'Shorts'])

    def test_genre_implies_fiction_and_fiction_rules_out_nonfiction(self):
        self.assertEqual(tags.normalise(['Detective and mystery stories', 'London (England)--Fiction',
                                         'World War']), ['Fiction', 'Mystery'])

    def test_plays_and_poems_are_not_nonfiction(self):
        self.assertEqual(tags.normalise(['Greek drama (Tragedy) -- Translations into English']), ['Drama'])
        self.assertEqual(tags.normalise(['Homer -- Translations into English', 'Epic poetry']), ['Poetry'])

    def test_junk_maps_to_nothing(self):
        for junk in ('BAFOMXdOUFc', 'DAGc9Nm790I', '9781432561857', '1432561855', '1809-1849',
                     'http://archive.org/details/CAT80737463', 'January 2015', 'None', '0'):
            self.assertEqual(tags.map_tag(junk), set(), junk)

    def test_eleven_letter_words_are_not_mistaken_for_video_ids(self):
        self.assertEqual(tags.map_tag('Agriculture'), {'Farming'})
        self.assertEqual(tags.map_tag('Bookkeeping'), {'Bookkeeping'})

    def test_trade_keywords_inside_fiction_are_not_trades(self):
        self.assertEqual(tags.normalise(['Farm life--Fiction', 'Fishing']), ['Fiction', 'Hunting and Fishing'])
        self.assertEqual(tags.map_tag('Whaling ships--Fiction'), {'Fiction'})
        self.assertEqual(tags.map_tag('kiln drying wood'), {'Forestry'})

    def test_every_tag_maps_only_into_the_vocabulary(self):
        allowed = set(tags.COLLECTIONS + tags.TRADES + tags.SUBJECTS)
        for pattern, out in tags.RULES:
            self.assertTrue(set(out) <= allowed, pattern.pattern)


class EpubTests(unittest.TestCase):
    def test_reads_se_and_lcsh_subjects_and_tolerates_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = epub(Path(tmp) / 'a.epub', ['Whaling--Fiction'], ['Adventure'])
            self.assertEqual(tags.epub_subjects(good), ['Adventure', 'Whaling--Fiction'])
            bad = Path(tmp) / 'b.epub'
            bad.write_bytes(b'not a zip')
            self.assertEqual(tags.epub_subjects(str(bad)), [])

    def test_plan_gives_collection_only_books_a_subject_from_their_epub(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = epub(Path(tmp) / 'a.epub', se_subjects=['Fiction', 'Horror'])
            books = [{'id': 1, 'title': 'Dracula', 'tags': ['PiVault Americana'], 'formats': [book]},
                     {'id': 2, 'title': 'Clean', 'tags': ['Farming', 'PiVault Trades'], 'formats': []},
                     {'id': 3, 'title': 'Junk', 'tags': ['BAFOMXdOUFc'], 'formats': []}]
            changes, untagged = tags.plan(books)
        self.assertEqual(changes, {1: ('Dracula', ['PiVault Americana'],
                                       ['Fiction', 'Horror', 'PiVault Americana'])})
        self.assertEqual(untagged, [(3, 'Junk')])


class ApplyRestoreTests(unittest.TestCase):
    def test_apply_backs_up_then_restore_puts_old_tags_back(self):
        library = {1: ['Adventure stories', 'Pirates--Fiction'], 2: ['PiVault Trades', 'Welding']}
        calls = []

        def fake(lib, *args):
            calls.append(args)
            if args[0] == 'list':
                return json.dumps([{'id': i, 'title': str(i), 'tags': t, 'formats': []}
                                   for i, t in library.items()])
            book_id = int(args[-1])
            value = args[2].partition(':')[2]
            library[book_id] = [t for t in value.split(',') if t]
            return ''

        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / 'Books'
            lib.mkdir()
            out = io.StringIO()
            with patch.object(tags, 'calibredb', fake), patch.object(tags, 'BOOKS_LOCK', str(Path(tmp) / 'no/lock')), \
                    contextlib.redirect_stdout(out):
                tags.main(['--library', str(lib)])
                self.assertEqual(library[1], ['Adventure stories', 'Pirates--Fiction'])  # dry run
                tags.main(['--library', str(lib), '--apply'])
                self.assertEqual(library[1], ['Adventure', 'Fiction'])
                self.assertEqual(library[2], ['PiVault Trades', 'Welding'])
                self.assertEqual([c[-1] for c in calls if c[0] == 'set_metadata'], ['1'])  # unchanged book skipped
                backup = next((lib.parent / '.pivault-tag-backups').glob('tags-*.json'))
                tags.main(['--library', str(lib), '--restore', str(backup)])
            self.assertEqual(library[1], ['Adventure stories', 'Pirates--Fiction'])


if __name__ == '__main__':
    unittest.main()
