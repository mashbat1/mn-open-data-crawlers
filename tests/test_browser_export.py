"""Optional real-browser integration checks; all network responses are local fixtures."""
import importlib.util
import json
from pathlib import Path
import unittest


@unittest.skipUnless(importlib.util.find_spec("playwright"), "optional browser check requires playwright + Chrome")
class BrowserTests(unittest.TestCase):
    def run_export(self, blocked=False, multi=False, checkpoint=False):
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
                    if "/api/invitation" in url:
                        body = {"results": [{"invitationId": 101, "supplierName": "Public company", "token": "must-not-export"}]}
                        if "page=2" not in url:
                            body["next"] = "/api/invitation?page=2"
                        r.fulfill(content_type="application/json", body=json.dumps(body))
                    elif "page=2" in url:
                        if blocked:
                            r.fulfill(status=403, content_type="text/html; charset=utf-8", body="Just a moment...")
                        else:
                            r.fulfill(content_type="text/html; charset=utf-8", body='<main>Нийт: 2 <a href="/mn/invitation/detail/102">Bridge</a></main>')
                    elif "/detail/" in url:
                        r.fulfill(content_type="text/html; charset=utf-8", body='<main><h1>Public invitation</h1><a href="/uploads/doc.pdf">Attachment</a></main>')
                    elif any("/mn/" + s in url for s in ("plan", "result", "contract", "implementation")):
                        section = url.split("/mn/")[1].split("?")[0]
                        r.fulfill(content_type="text/html; charset=utf-8",
                                  body=f'<main><a href="/mn/{section}/detail/999">Public detail</a></main>')
                    else:
                        body = '<main>Нийт: 2 <a href="/mn/invitation/detail/101">Road</a><a href="/mn/invitation?years=2026&page=2">2</a></main>'
                        if multi:
                            body = '<nav>' + "".join(f'<a href="/mn/{s}">{s}</a>' for s in ("plan", "result", "contract", "implementation")) + '</nav>' + body
                            body += '<a href="/mn/contract/create">Do not follow</a><a href="/mn/result?token=private">Do not follow</a>'
                        if checkpoint:
                            body = '<main>Нийт: 55 ' + "".join(f'<a href="/mn/invitation/detail/{n}">Road</a>' for n in range(100, 155)) + '</main>'
                        r.fulfill(content_type="text/html; charset=utf-8", body=body)

                page.route("https://www.tender.gov.mn/**", route)
                page.goto("https://www.tender.gov.mn/mn/invitation?years=2026")
                page.evaluate("""() => {
                  window.exportLines = []; window.exportClosed = false; window.commits = 0; window.seeks = [];
                  const originalTimeout = window.setTimeout.bind(window);
                  window.setTimeout = (fn, ms, ...args) => originalTimeout(fn, ms === 2000 ? 0 : ms, ...args);
                  window.showSaveFilePicker = async () => ({createWritable: async () => ({
                    write: async line => window.exportLines.push(line),
                    seek: async position => window.seeks.push(position),
                    close: async () => { window.exportClosed = true; window.commits++; }
                  })});
                }""")
                if multi:
                    page.evaluate("""() => {
                      performance.getEntriesByType = () => [
                        {name: 'https://www.tender.gov.mn/api/invitation?page=1'},
                        {name: 'https://www.tender.gov.mn/api/profile'},
                        {name: 'https://www.tender.gov.mn/api/invitation?token=private'}];
                    }""")
                page.add_script_tag(content=script.read_text(encoding="utf-8"))
                page.get_by_role("button", name="Тендерийн өгөгдөл экспортлох").click()
                page.wait_for_function("window.exportLines.length && JSON.parse(window.exportLines.at(-1)).type === 'manifest' && !document.querySelector('#tender-public-export button').disabled", timeout=20000)
                if checkpoint:
                    self.assertGreater(page.evaluate("window.commits"), 1)
                    self.assertGreater(page.evaluate("window.seeks[0]"), 0)
                return [json.loads(line) for line in page.evaluate("window.exportLines")], requests
            finally:
                browser.close()

    def test_paginates_fetches_details_and_never_downloads_attachments(self):
        records, requests = self.run_export()
        self.assertFalse(records[-1]["complete"])
        self.assertTrue(records[-1]["scope_complete"])
        self.assertEqual(records[-1]["coverage"]["2026"]["unique_invitations"], 2)
        self.assertEqual(len([r for r in records if r.get("kind") == "invitation_detail"]), 2)
        self.assertFalse(any("/uploads/" in url for url in requests))
        self.assertEqual(records[-1]["records"], len(records) - 1)

    def test_security_challenge_keeps_partial_export_and_stops(self):
        records, requests = self.run_export(blocked=True)
        self.assertFalse(records[-1]["complete"])
        self.assertTrue(records[-1]["stopped"])
        self.assertIn("HTTP 403", records[-1]["errors"][-1])
        self.assertEqual(len([u for u in requests if "page=2" in u]), 1)

    def test_follows_other_public_sections_and_observed_api_but_excludes_private_routes(self):
        records, requests = self.run_export(multi=True)
        self.assertEqual(records[-1]["missing_sections"], [])
        for section in ("plan", "result", "contract", "implementation"):
            self.assertTrue(any(r.get("kind") == section + "_detail" for r in records))
        self.assertEqual(len([r for r in records if r["type"] == "api_response"]), 2)
        self.assertNotIn("must-not-export", json.dumps(records))
        self.assertFalse(any("/create" in u or "token=" in u or "/api/profile" in u for u in requests))
        self.assertFalse(records[-1]["complete"])

    def test_commits_long_export_checkpoints_and_continues_writing(self):
        records, _ = self.run_export(checkpoint=True)
        self.assertEqual(len([r for r in records if r.get("kind") == "invitation_detail"]), 55)
        self.assertEqual(records[-1]["records"], len(records) - 1)


if __name__ == "__main__":
    unittest.main()
