import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('music_source', Path(__file__).with_name('music-source.py'))
music_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(music_source)

MD5 = 'a'*32
METADATA = {'metadata': {'licenseurl': 'http://creativecommons.org/publicdomain/zero/1.0/', 'creator': 'Tester'},
            'files': [{'name': 'b.mp3', 'format': 'VBR MP3', 'size': '200', 'md5': MD5},
                      {'name': 'a.mp3', 'format': 'VBR MP3', 'size': '300', 'md5': MD5},
                      {'name': 'a_64kb.mp3', 'format': '64Kbps MP3', 'size': '900', 'md5': MD5},
                      {'name': 'c.mp3', 'format': 'VBR MP3', 'size': '', 'md5': MD5},
                      {'name': 'd.mp3', 'format': 'VBR MP3', 'size': '10', 'md5': 'bad'}]}


class SelectTests(unittest.TestCase):
    def test_album_takes_every_verifiable_vbr_mp3(self):
        self.assertEqual([f['name'] for f in music_source.select(METADATA, 'album')], ['a.mp3', 'b.mp3'])

    def test_track_takes_largest_vbr_mp3(self):
        self.assertEqual([f['name'] for f in music_source.select(METADATA, 'track')], ['a.mp3'])

    def test_no_files_selects_nothing(self):
        self.assertEqual(music_source.select({'files': []}, 'track'), [])


class RightsTests(unittest.TestCase):
    def test_dossier_preserves_metadata_and_cc0_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'evidence').mkdir()
            (root/'evidence/CC0-1.0.txt').write_text('cc0')
            (root/'meta.json').write_text(json.dumps(METADATA))
            record = {'key': 'k', 'artist': 'A', 'title': 'T', 'year': '-', 'archive_id': 'id', 'rights': 'basis'}
            music_source.write_rights(root/'meta.json', root/'out', root/'evidence', record,
                                      music_source.select(METADATA, 'album'))
            self.assertEqual(json.loads((root/'out/source-metadata.json').read_text()), METADATA)
            self.assertTrue((root/'out/CC0-1.0.txt').exists())
            self.assertIn('A — T — rights', (root/'out/RIGHTS.txt').read_text())


if __name__ == '__main__':
    unittest.main()
