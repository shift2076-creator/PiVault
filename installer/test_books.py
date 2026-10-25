from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'jobs/books/PiVault-Books.sh'
ROW = ('sample-pg1\tSample Book\tSample Author\t1900\tgutenberg\thttps://www.gutenberg.org/ebooks/1.epub3.images'
       '\t-\t-\t-\t-\t-\tProject Gutenberg eBook 1')


BAD = ROW.replace('sample-pg1', 'bad-pg2').replace('Sample Book', 'Bad Book')
ADD = 'if [[ $1 == add ]]; then echo "Added book ids: 7"; fi\nexit 0\n'


class BooksRoundTests(unittest.TestCase):
    """Run run_round against a stand-in calibredb; nothing touches the network or a real library."""

    def round(self, calibredb, rounds=1, fetch='printf epub > "$4"', reachable=0, rows=(ROW,)):
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
sources_reachable() {{ return {reachable}; }}
queue() {{ printf '%s\\n' {' '.join("$'" + r + "'" for r in rows)}; }}
fetch() {{ {fetch}; }}
status=0
for _ in $(seq {rounds}); do run_round test || status=$?; done
exit $status
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
        result, done, root = self.round('echo "database is locked" >&2\nexit 0\n')
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(done)
        self.assertEqual((root / 'install/state/sample-pg1.failed').read_text().strip(), '1')
        self.assertIn('database is locked', result.stderr)

    def test_failing_title_does_not_fail_round_and_is_parked_after_three_rounds(self):
        fetch = '[[ $4 == *bad-pg2* ]] && return 1; printf epub > "$4"'
        result, _, root = self.round(ADD, rounds=1, fetch=fetch, rows=(ROW, BAD))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root / 'install/state/bad-pg2.failed').read_text().strip(), '1')
        result, _, root = self.round(ADD, rounds=4, fetch=fetch, rows=(ROW, BAD))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((root / 'install/state/bad-pg2.parked').exists())
        self.assertFalse((root / 'install/state/bad-pg2.failed').exists())
        self.assertEqual(result.stdout.count('Bad Book (1900), Sample Author'), 3)
        self.assertIn('0 incomplete (retried later); 1 parked.', result.stdout.splitlines()[-1])

    def test_unreachable_sources_fail_round_without_counting_attempts(self):
        result, _, root = self.round(ADD, rounds=3, fetch='return 1', reachable=1)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((root / 'install/state/sample-pg1.parked').exists())
        self.assertFalse((root / 'install/state/sample-pg1.failed').exists())
        self.assertIn('Download sources are unreachable', result.stdout)


if __name__ == '__main__':
    unittest.main()
