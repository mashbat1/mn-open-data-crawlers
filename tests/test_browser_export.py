"""Optional real-browser integration checks; all network responses are local fixtures."""
import importlib.util
import json
from pathlib import Path
import unittest


@unittest.skipUnless(importlib.util.find_spec("playwright"), "optional browser check requires playwright + Chrome")
class BrowserTests(unittest.TestCase):
    def run_export(self, blocked=False):
        from playwright.sync_api import sync_playwright
        script = Path(__file__).resolve().parents[1] / "browser_export.js"
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                requests = []

                def route(r):
                    url = r.request.url
                    requests.append(url)
                    if "page=2" in url:
                        if blocked:
                            r.fulfill(status=403, content_type="text/html; charset=utf-8", body="Just a moment...")
                        else:
                            r.fulfill(content_type="text/html; charset=utf-8", body='<main>Нийт: 2 <a href="/mn/invitation/detail/102">Bridge</a></main>')
                    elif "/detail/" in url:
                        r.fulfill(content_type="text/html; charset=utf-8", body='<main><h1>Public invitation</h1><a href="/uploads/doc.pdf">Attachment</a></main>')
                    else:
                        r.fulfill(content_type="text/html; charset=utf-8", body='<main>Нийт: 2 <a href="/mn/invitation/detail/101">Road</a>'
                                  '<a href="/mn/invitation?years=2026&page=2">2</a></main>')

                page.route("https://www.tender.gov.mn/**", route)
                page.goto("https://www.tender.gov.mn/mn/invitation?years=2026")
                page.evaluate("""() => {
                  window.exportLines = []; window.exportClosed = false;
                  window.showSaveFilePicker = async () => ({createWritable: async () => ({
                    write: async line => window.exportLines.push(line),
                    close: async () => { window.exportClosed = true; }
                  })});
                }""")
                page.add_script_tag(content=script.read_text(encoding="utf-8"))
                page.get_by_role("button", name="Тендерийн өгөгдөл экспортлох").click()
                page.wait_for_function("window.exportClosed", timeout=20000)
                return [json.loads(line) for line in page.evaluate("window.exportLines")], requests
            finally:
                browser.close()

    def test_paginates_fetches_details_and_never_downloads_attachments(self):
        records, requests = self.run_export()
        self.assertTrue(records[-1]["complete"])
        self.assertEqual(records[-1]["coverage"]["2026"]["unique_invitations"], 2)
        self.assertEqual(len([r for r in records if r.get("kind") == "invitation_detail"]), 2)
        self.assertFalse(any("/uploads/" in url for url in requests))
        self.assertEqual(records[-1]["records"], len(records) - 1)

    def test_security_challenge_keeps_partial_export_and_stops(self):
        records, requests = self.run_export(blocked=True)
        self.assertFalse(records[-1]["complete"])
        self.assertTrue(records[-1]["stopped"])
        self.assertIn("HTTP 403", records[-1]["errors"][-1])
        self.assertFalse(any("/detail/" in url for url in requests))


if __name__ == "__main__":
    unittest.main()
