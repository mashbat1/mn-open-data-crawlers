import unittest
from unittest.mock import patch
from types import SimpleNamespace
from crawlers.eiti import data_rows, last_offset, company_page


class EitiPaginationTests(unittest.TestCase):
    def test_public_directory_pages_do_not_inherit_a_changed_session_filter(self):
        cookies = {}
        def post(url, params, data):
            result = "filtered directory" if cookies else f"shared page {params['page']}"
            cookies["PHPSESSID"] = "anonymous-fixture"
            return SimpleNamespace(text=result)
        with patch("crawlers.eiti.http._session", return_value=SimpleNamespace(cookies=cookies)), patch(
                "crawlers.eiti.http.post", side_effect=post):
            self.assertEqual(company_page(0), "shared page 0")
            self.assertEqual(company_page(10), "shared page 10")
    def test_explicit_empty_ajax_result_is_valid_but_arbitrary_html_is_not(self):
        self.assertEqual(data_rows('<div class="alert">Мэдээлэл олдсонгүй...</div><script>paginate()</script>'), [])
        with self.assertRaises(RuntimeError):
            data_rows("<html>Temporary service failure</html>")

    def test_native_final_page_offset_is_read_from_callback(self):
        self.assertEqual(last_offset('<a onclick="companyList(2150,null,)">Last</a>', "companyList"), 2150)
        self.assertIsNone(last_offset("<script>function companyList(page){}</script>", "companyList"))
