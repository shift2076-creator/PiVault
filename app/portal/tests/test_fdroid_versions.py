from unittest.mock import patch

from django.test import SimpleTestCase

from portal.client_packages import _discover_fdroid


def _index(package_id, version_name):
    return {'packages': {package_id: {'versions': {'h': {
        'manifest': {'versionName': version_name, 'versionCode': 1},
        'file': {'name': '/' + package_id + '_1.apk', 'sha256': 'a' * 64, 'size': 1}}}}}}


class FdroidBuildSuffixTests(SimpleTestCase):
    def test_build_suffixes_are_stripped_before_version_check(self):
        cases = (('4.5.21-ose', '4.5.21'), ('2026.09.29-32-FDroid', '2026.09.29-32'))
        for raw, expected in cases:
            with self.subTest(raw=raw), patch('portal.client_packages.fdroid_index',
                    return_value=_index('org.example', raw)):
                self.assertEqual(_discover_fdroid('org.example')['version'], expected)
