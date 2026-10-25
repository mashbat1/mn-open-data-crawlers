import unittest
from unittest.mock import patch
from crawlers.companies import lifecycle_preflight


class RegistryServiceTests(unittest.TestCase):
    def test_error_payload_is_not_an_empty_successful_register(self):
        with patch("crawlers.companies.Http.json", return_value={"error": "maintenance"}):
            with self.assertRaises(RuntimeError):
                lifecycle_preflight()

    def test_positive_total_requires_a_row(self):
        with patch("crawlers.companies.Http.json", return_value={"recordsTotal": 10, "data": []}):
            with self.assertRaises(RuntimeError):
                lifecycle_preflight()

    def test_valid_public_table_allows_resume(self):
        with patch("crawlers.companies.Http.json", return_value={"recordsTotal": 10, "data": [["123"]]}):
            self.assertEqual(lifecycle_preflight(), 10)
