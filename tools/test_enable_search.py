import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('enable_search', Path(__file__).parent / 'enable_search.py')
enable_search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enable_search)


class PlanTests(unittest.TestCase):
    def test_wiki_always_added_and_apps_without_credentials_skipped(self):
        result, added = enable_search.plan({'navidrome': {'admin_username': 'svc'}})
        self.assertEqual(added, ['wiki', 'music'])
        self.assertNotIn('videos', result['search'])
        self.assertEqual(result['search']['wiki']['origin'], 'http://kiwix:8080')

    def test_existing_entries_are_kept_and_input_not_modified(self):
        native = {'jellyfin': {'api_key': 'k'}, 'search': {'wiki': {'origin': 'http://x', 'link': 'http://y'}}}
        result, added = enable_search.plan(native)
        self.assertEqual(added, ['videos'])
        self.assertEqual(result['search']['wiki']['origin'], 'http://x')
        self.assertEqual(native['search'], {'wiki': {'origin': 'http://x', 'link': 'http://y'}})

    def test_non_object_search_is_rejected(self):
        with self.assertRaises(ValueError):
            enable_search.plan({'search': []})

    def test_removing_failed_apps_drops_empty_section(self):
        result = enable_search.without({'search': {'wiki': {}}}, ['wiki'])
        self.assertNotIn('search', result)

    def test_probe_output_ignores_unrelated_lines(self):
        status = enable_search.parse_probe('noise\nPIVAULT_SEARCH wiki ok\nPIVAULT_SEARCH music fail\n')
        self.assertEqual(status, {'wiki': True, 'music': False})


if __name__ == '__main__':
    unittest.main()
