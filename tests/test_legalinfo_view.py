import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from crawlers import legalinfo
from crawlers.core import Ctx


class LegalViewTests(unittest.TestCase):
    def test_modern_act_without_inner_class_is_still_read(self):
        html = '<div class="main-huuliin-content"><div class="nom-more-header">Toolbar</div><p>Actual law</p><a class="print-zuil">Print</a></div><footer>Unrelated</footer>'
        self.assertEqual(legalinfo.act_text(html), ("Actual law", "main-huuliin-content"))

    def test_empty_act_does_not_become_toolbar_or_footer_text(self):
        html = '<div class="main-huuliin-content"><div class="law_content"><div class="nom-more-header">Toolbar</div></div></div><footer>Unrelated</footer>'
        self.assertEqual(legalinfo.act_text(html), ("", None))

    def test_failed_default_view_retries_official_original_html(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = Ctx("legalinfo", directory)
            ctx.state.update(metadata_parser_version=2, stages_complete=["totals"], totals={},
                             law_ids={"10147": 33}, listed={})
            ctx.state["errors"]["detail"] = {"10147": "HTTP 500"}
            ctx.state["attempts"]["detail"] = {"10147": 3}
            response = Mock(text='<div class="law_content"><p>Official act text</p></div>',
                            url="https://legalinfo.mn/mn/detail?lawId=10147&showType=1")
            try:
                with patch.object(legalinfo.http, "get", return_value=response) as get:
                    self.assertTrue(legalinfo.crawl(ctx))
                    self.assertEqual(get.call_args.kwargs["params"], {"lawId": "10147", "showType": 1})
            finally:
                ctx.close()
            with gzip.open(next(Path(ctx.parts).glob("texts.*.gz")), "rt", encoding="utf-8") as source:
                row = json.loads(next(source))
            self.assertEqual(row["html_view"], "showType=1")
            self.assertEqual(row["text"], "Official act text")
