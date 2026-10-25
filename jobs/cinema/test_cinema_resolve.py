import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('resolver', pathlib.Path(__file__).with_name('cinema-resolve.py'))
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)


def mp4(name, minutes, height=480, size=500_000_000):
    return {'name': name, 'size': str(size), 'md5': 'a' * 32, 'length': str(minutes * 60), 'height': str(height)}


class AcceptableTests(unittest.TestCase):
    def check(self, wanted, wanted_year, **doc):
        doc.setdefault('identifier', 'x')
        return resolver.acceptable(doc, wanted, wanted_year, resolver.words(wanted))

    def test_original_release_with_star_prefix(self):
        self.assertTrue(self.check('The Kid', 1921, title='Charlie Chaplin - The Kid (1921)'))

    def test_reissue_year_in_label_is_rejected(self):
        self.assertFalse(self.check('The Gold Rush', 1925, title='The Gold Rush (1942 narrated version)', year='1925'))

    def test_dubs_and_colorized_copies_are_rejected(self):
        self.assertFalse(self.check('Intolerance', 1916, title='Intolerance 1916 doblada'))
        self.assertFalse(self.check('Wings', 1927, title='Wings (1927) colorized'))

    def test_foreign_language_metadata_is_rejected(self):
        self.assertFalse(self.check('Greed', 1924, title='Greed', year='1924', language='spa'))

    def test_year_evidence_is_required(self):
        self.assertFalse(self.check('Peter Pan', 1924, title='Peter Pan'))
        self.assertFalse(self.check('Peter Pan', 1924, title='Peter Pan', year='1953'))
        self.assertTrue(self.check('Peter Pan', 1924, title='Peter Pan', date='1924-12-29'))

    def test_title_word_in_query_is_not_treated_as_a_rejection(self):
        self.assertTrue(self.check('Blacksmith Scene', 1893, title='Blacksmith Scene (1893)'))


class BestFileTests(unittest.TestCase):
    def test_highest_resolution_full_length_copy_wins(self):
        files = [mp4('film_512kb.mp4', 95, 240), mp4('film.mp4', 94, 480), mp4('film_trailer.mp4', 2, 720),
                 mp4('film_excerpt.mp4', 20, 1080)]
        url, size, md5, minutes, height = resolver.best_file('item id', files, 95)
        self.assertEqual(url, 'https://archive.org/download/item%20id/film.mp4')
        self.assertEqual((height, minutes), (480, 94.0))

    def test_copy_without_runtime_cannot_be_confirmed(self):
        file = mp4('film.mp4', 95)
        del file['length']
        self.assertIsNone(resolver.best_file('i', [file], 95))
        self.assertIsNotNone(resolver.best_file('i', [file], 0))

    def test_missing_checksum_is_rejected(self):
        file = mp4('film.mp4', 95)
        file['md5'] = ''
        self.assertIsNone(resolver.best_file('i', [file], 95))


class ResolveTests(unittest.TestCase):
    def test_first_acceptable_item_with_a_matching_file(self):
        docs = [{'identifier': 'wrong', 'title': 'Wings (1966)'},
                {'identifier': 'right', 'title': 'Wings', 'year': '1927'}]
        meta = {'right': {'metadata': {'title': 'Wings', 'year': '1927'}, 'files': [mp4('Wings.mp4', 140)]}}
        found = resolver.resolve('Wings', 1927, 144, fetch=lambda url: meta[url.rsplit('/', 1)[1]],
                                 searcher=lambda title, year, with_year: docs)
        self.assertEqual(found[0], 'right')

    def test_nothing_safe_returns_none(self):
        self.assertIsNone(resolver.resolve('Wings', 1927, 144, fetch=None,
                                           searcher=lambda title, year, with_year: []))


if __name__ == '__main__':
    unittest.main()
