import unittest
from unittest.mock import patch
from crawlers.ebarimt import lookup


class LookupTests(unittest.TestCase):
    def test_tin_not_found_does_not_invent_merchant_or_receipt_status(self):
        with patch("crawlers.ebarimt.http.json", side_effect=[
                RuntimeError("legacy HTTP 500"),
                {"status": 500, "data": None, "msg": "Хэрэглэгчийн мэдээлэл олдсонгүй."}]):
            result = lookup("1042262")
        self.assertIsNone(result["found"])
        self.assertFalse(result["tin_found"])
        self.assertFalse(result["receipt_status_known"])

    def test_unexplained_upstream_error_is_not_a_not_found_result(self):
        with patch("crawlers.ebarimt.http.json", side_effect=[
                RuntimeError("legacy HTTP 500"), {"status": 500, "data": None, "msg": "Internal Server Error"}]):
            with self.assertRaises(RuntimeError):
                lookup("1042262")

    def test_official_profile_preserves_provenance_and_unknown_receipt_fields(self):
        with patch("crawlers.ebarimt.http.json", side_effect=[
                RuntimeError("legacy HTTP 500"), {"status": 200, "data": 12345678901},
                {"status": 200, "data": {"found": True, "name": "Company", "vatPayer": True, "cityPayer": False}}]):
            result = lookup("1234567")
        self.assertTrue(result["found"])
        self.assertEqual(result["found_scope"], "tax_registry")
        self.assertFalse(result["receipt_status_known"])
