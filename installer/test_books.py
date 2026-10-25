from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'jobs/books/PiVault-Books.sh'
ROW = ('sample-pg1\tSample Book\tSample Author\t1900\tgutenberg\thttps://www.gutenberg.org/ebooks/1.epub3.images'
       '\t-\t-\t-\t-\t-\tProject Gutenberg eBook 1')


class BooksRoundTests(unittest.TestCase):
    """Run run_round against a stand-in calibredb; nothing touches the network or a real library."""

    def round(self, calibredb):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'bin').mkdir()
        (root / 'install').mkdir()
        (root / 'library').mkdir()
        (root / 'install/books.conf').write_text('DAILY_COUNT=5\n')
        stub = root / 'bin/calibredb'
        stub.write_text('#!/usr/bin/env bash\n' + calibredb)
        stub.chmod(0o755)
        harness = f'''
source "{SCRIPT}"
INSTALL_DIR="{root}/install"; LIBRARY="{root}/library"; INCOMING="{root}/incoming"
host_check() {{ :; }}
in_library() {{ return 1; }}
queue() {{ printf '%s\\n' $'{ROW}'; }}
fetch() {{ printf 'epub' > "$4"; }}
run_round test
'''
        env = dict(os.environ, PATH=f'{root}/bin:' + os.environ['PATH'])
        result = subprocess.run(['bash', '-c', harness], capture_output=True, text=True, env=env)
        done = root / 'install/state/sample-pg1.done'
        return result, (done.read_text() if done.exists() else None), root

    def test_existing_title_and_author_is_recorded_once_not_retried(self):
        result, done, root = self.round(
            'echo "The following books were not added as they already exist in the database '
            '(see --duplicates option or --automerge option):" >&2\nexit 0\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(done.startswith('existing\tgutenberg\t'))
        self.assertIn('Already in the library under the same title and author: Sample Book (1900).', result.stdout)
        self.assertEqual(list((root / 'incoming').iterdir()), [])

    def test_new_book_is_added(self):
        result, done, _ = self.round(
            'if [[ $1 == add ]]; then echo "Added book ids: 7"; fi\nexit 0\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(done.split('\t')[1] == 'gutenberg')
        self.assertIn('Added: Sample Book (1900).', result.stdout)

    def test_other_calibre_failure_stays_queued(self):
        result, done, _ = self.round('echo "database is locked" >&2\nexit 0\n')
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(done)
        self.assertIn('database is locked', result.stderr)


if __name__ == '__main__':
    unittest.main()
