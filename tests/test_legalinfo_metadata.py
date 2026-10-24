from crawlers.legalinfo import ActListParser
import unittest


def check_nested_cells_do_not_capture_next_cell_or_footer():
    parser = ActListParser()
    parser.feed('''<div class="legal-list-component other">
      <div data-block="actnumber"><span title="105">105</span></div>
      <div data-block="title"><div><a class="act-name fw" href="/mn/detail?lawId=10147">A &amp; B</a></div>
        <a href="/mn/detail?lawId=10147&amp;type=3">1</a></div>
      <div data-block="enacteddate"><div><span>2014-03-28</span></div></div>
      <div data-block="isvalid"><i></i></div></div>
      <style>footer css</style><div>unrelated footer</div>''')
    assert parser.rows == [{"law_id": "10147", "number": "105", "title": "A & B", "enacted": "2014-03-28",
                            "blocks": {"enacteddate": "2014-03-28", "isvalid": ""}}]


class LegalMetadataTests(unittest.TestCase):
    def test_nested_cells(self):
        check_nested_cells_do_not_capture_next_cell_or_footer()
