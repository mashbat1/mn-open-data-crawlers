import os
import tempfile
import unittest
from unittest.mock import patch

from crawlers.core import Ctx
from crawlers import tender


class TenderTests(unittest.TestCase):
    def test_no_credentials_never_starts_a_request(self):
        with patch.dict(os.environ, {"TENDER_API_TOKEN": ""}), patch.object(tender.http, "json") as request:
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                tender.read_page("invitation", 1)
            request.assert_not_called()

    def test_token_cannot_be_sent_to_nonofficial_or_insecure_host(self):
        for base in ["http://opendata.tender.gov.mn/api", "https://tender.gov.mn.example.com/api",
                     "https://user:password@tender.gov.mn/api", "https://tender.gov.mn/api?token=secret"]:
            with self.subTest(base=base), patch.dict(os.environ, {"TENDER_API_BASE": base}):
                with self.assertRaises(RuntimeError):
                    tender.api_base()

    def test_empty_page_before_total_is_an_error(self):
        with patch.dict(os.environ, {"TENDER_API_TOKEN": "test"}), patch.object(
                tender.http, "json", return_value={"return_code": 200, "total": 10, "data": []}):
            with self.assertRaisesRegex(RuntimeError, "empty page"):
                tender.read_page("invitation", 1)

    def test_duplicates_do_not_satisfy_reported_total(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = Ctx("tender", d)
            try:
                with patch.dict(os.environ, {"TENDER_API_TOKEN": "test"}), patch.object(
                        tender, "read_page", return_value=(2, [{"invitationId": 1}, {"invitationId": 1}])):
                    with self.assertRaisesRegex(RuntimeError, "1 unique IDs vs 2"):
                        tender.crawl(ctx)
                self.assertFalse(ctx.stage_done("tender_invitations"))
                self.assertEqual(ctx.done_set("tender_invitations_pages"), set())
            finally:
                ctx.close()

    def test_complete_crawl_writes_lists_and_details(self):
        data = {"invitation": (1, [{"invitationId": 123, "tenderName": "Public work"}]),
                "plan": (1, [{"tenderId": 456}]),
                "invitation/123/suppliers": (1, [{"supplierRegister": "1234567"}]),
                "invitation/123/committee": (0, [])}
        with tempfile.TemporaryDirectory() as d:
            ctx = Ctx("tender", d)
            try:
                with patch.dict(os.environ, {"TENDER_API_TOKEN": "test"}), patch.object(
                        tender, "read_page", side_effect=lambda path, page: data[path]):
                    self.assertTrue(tender.crawl(ctx))
                self.assertEqual(ctx.sinks["invitations"].n, 1)
                self.assertEqual(ctx.sinks["suppliers"].n, 1)
                self.assertEqual(ctx.state["tender_counts"]["123:committee"], 0)
            finally:
                ctx.close()


if __name__ == "__main__":
    unittest.main()
