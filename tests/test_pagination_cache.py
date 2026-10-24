import tempfile
import unittest
from unittest.mock import patch
from crawlers.shilendans_common import pages_all


class CacheTests(unittest.TestCase):
    def test_smaller_inferred_last_page_total_cannot_silently_replace_the_initial_count(self):
        class Client:
            def json(self, url, params):
                return {"content": [{"id": params["page"]}],
                        "totalElements": 5 if params["page"] == 0 else 2, "totalPages": 5}
        with self.assertRaisesRegex(RuntimeError, "5 -> 2"):
            pages_all(Client(), "https://example.test", {}, size=1)
    def test_retry_reuses_successful_pages_after_later_page_fails(self):
        calls = []
        class Client:
            fail = True
            def json(self, url, params):
                n = params["page"]
                calls.append(n)
                if n == 1 and self.fail:
                    raise RuntimeError("temporary server error")
                return {"content": [{"id": 1}, {"id": 2}] if n == 0 else [{"id": 3}],
                        "totalElements": 3, "totalPages": 2}
        client = Client()
        with tempfile.TemporaryDirectory() as d:
            with patch("crawlers.shilendans_common.slices", side_effect=RuntimeError("unavailable")):
                with self.assertRaises(RuntimeError):
                    pages_all(client, "https://example.test", {}, size=2, cache_dir=d)
            client.fail = False
            rows, total = pages_all(client, "https://example.test", {}, size=2, cache_dir=d)
            self.assertEqual([r["id"] for r in rows], [1, 2, 3])
            self.assertEqual(total, 3)
            self.assertEqual(calls, [0, 1, 1])
