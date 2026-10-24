import unittest
from unittest.mock import patch
from crawlers.parliament import pages


class PaginationTests(unittest.TestCase):
    def test_server_page_size_cap_does_not_truncate_nested_pagination(self):
        responses = [
            {"results": [{"id": 1}, {"id": 2}], "pagination": {
                "page": 1, "page_size": 2, "total_items": 3, "has_next": True}},
            {"results": [{"id": 3}], "pagination": {
                "page": 2, "page_size": 2, "total_items": 3, "has_next": False}}]
        requested = []
        def get(url, params):
            requested.append(dict(params))
            return responses.pop(0)
        with patch("crawlers.parliament.http.json", side_effect=get):
            rows = [r for _, rs in pages("https://parliament.mn/api/test", page_size=100) for r in rs]
        self.assertEqual([r["id"] for r in rows], [1, 2, 3])
        self.assertEqual([p["page"] for p in requested], [1, 2])

    def test_nested_pagination_mismatch_is_not_complete(self):
        body = {"data": [{"id": 1}], "pagination": {"page": 1, "total_items": 2, "has_next": False}}
        with patch("crawlers.parliament.http.json", return_value=body):
            with self.assertRaises(RuntimeError):
                list(pages("https://parliament.mn/api/test"))
