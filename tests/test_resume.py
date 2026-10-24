import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ci
from crawlers.core import Ctx, MAX_ATTEMPTS
from crawlers import eiti


class ResumeTests(unittest.TestCase):
    def test_exhausted_failure_is_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = Ctx("cabinet", d)
            ctx.state["attempts"]["pages"] = {"bad": MAX_ATTEMPTS}
            ctx.state["errors"]["pages"] = {"bad": "request failed"}
            called = []
            self.assertFalse(ctx.run_items("pages", ["bad"], called.append))
            self.assertEqual(called, [])
            self.assertEqual(ctx.state["errors"]["pages"], {"bad": "request failed"})

    def test_retry_clears_error_and_completes_generator(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = Ctx("cabinet", d)
            ctx.state["attempts"]["pages"] = {"retry": 1}
            ctx.state["errors"]["pages"] = {"retry": "old error"}
            self.assertTrue(ctx.run_items("pages", iter(["retry"]), lambda _: None))
            self.assertEqual(ctx.state["errors"]["pages"], {})

    def test_publication_rejects_incomplete_or_failed_state(self):
        for complete, errors in [(False, {}), (True, {"pages": {"bad": "failed"}})]:
            with self.subTest(complete=complete, errors=errors), tempfile.TemporaryDirectory() as d:
                Path(d, "state.json").write_text(json.dumps({"complete": complete, "errors": errors}))
                with patch.object(ci, "WORK", d), patch.object(ci, "gh") as remote:
                    with self.assertRaisesRegex(SystemExit, "incomplete"):
                        ci.finalize("cabinet")
                    remote.assert_not_called()

    def test_eiti_rejects_fallback_to_another_year(self):
        response = type("Response", (), {"text": '<footer data-year="2024">'})()
        with patch.object(eiti.http, "get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "requested year 2025"):
                eiti.select_year(2025)
            eiti.select_year(2024)


if __name__ == "__main__":
    unittest.main()
