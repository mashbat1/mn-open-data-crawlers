import unittest
from crawlers.eiti import data_rows, last_offset


class EitiPaginationTests(unittest.TestCase):
    def test_explicit_empty_ajax_result_is_valid_but_arbitrary_html_is_not(self):
        self.assertEqual(data_rows('<div class="alert">Мэдээлэл олдсонгүй...</div><script>paginate()</script>'), [])
        with self.assertRaises(RuntimeError):
            data_rows("<html>Temporary service failure</html>")

    def test_native_final_page_offset_is_read_from_callback(self):
        self.assertEqual(last_offset('<a onclick="companyList(2150,null,)">Last</a>', "companyList"), 2150)
        self.assertIsNone(last_offset("<script>function companyList(page){}</script>", "companyList"))
