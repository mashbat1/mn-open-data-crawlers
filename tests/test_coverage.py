import unittest
from coverage import assess


class CoverageTests(unittest.TestCase):
    def test_successful_release_can_still_have_missing_rows(self):
        result = assess({'tables': {'adb': {'rows': 1501}}, 'server_reported': {'adb': 1558}, 'failed_items': {}})
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['count_mismatches']['adb']['stored'], 1501)

    def test_failed_partition_prevents_published_status(self):
        self.assertEqual(assess({'failed_items': {'od': 3}})['status'], 'needs_attention')

    def test_verified_count_is_published_with_explicit_scope(self):
        result = assess({'tables': {'pages': {'rows': 1398}}, 'server_reported': {'pages': 1398},
                         'failed_items': {}, 'coverage_scope': 'sitemap pages only'})
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['coverage_scope'], 'sitemap pages only')
