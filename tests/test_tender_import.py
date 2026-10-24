import json
from pathlib import Path
import tempfile
import unittest

from import_tender_export import import_export, public_url


class ImportTests(unittest.TestCase):
    def export(self, folder, complete=False, verified=False):
        rows = [{"type": "metadata", "source": "https://www.tender.gov.mn"},
                {"type": "page", "kind": "invitation_list", "url": "https://www.tender.gov.mn/mn/invitation?years=2026",
                 "text": "Public invitation", "tables": [], "links": [
                     {"url": "https://www.tender.gov.mn/mn/invitation/detail/123", "name": "Road", "attachment": False},
                     {"url": "https://www.tender.gov.mn/uploads/contract.pdf", "name": "Contract", "attachment": True}]},
                {"type": "manifest", "records": 2, "complete": complete, "stopped": False, "errors": [],
                 "coverage": {"2026": {"verified": verified, "reported_total": 1, "unique_invitations": 1}}}]
        path = Path(folder, "export.jsonl")
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return path

    def test_partial_export_remains_partial_and_preserves_attachment_reference(self):
        with tempfile.TemporaryDirectory() as d:
            result = import_export(self.export(d), Path(d, "work"))
            self.assertFalse(result["complete"])
            self.assertEqual(result["rows"]["pages"], 1)
            self.assertEqual(result["rows"]["attachments"], 1)
            self.assertEqual(result["rows"]["invitation_links"], 1)

    def test_complete_flag_requires_verified_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(import_export(self.export(d, complete=True), Path(d, "work"))["complete"])

    def test_verified_export_records_scope_without_nationwide_claim(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d, "work")
            self.assertFalse(import_export(self.export(d, complete=True, verified=True), work)["complete"])
            state = json.loads((work / "state.json").read_text())
            self.assertFalse(state["coverage_scope"]["nationwide_completeness_verified"])
            self.assertIn("contract", state["coverage_scope"]["missing_sections"])

    def test_recovers_committed_checkpoint_without_a_false_complete_claim(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.export(d)
            path.write_text("\n".join(path.read_text().splitlines()[:-1]), encoding="utf-8")
            result = import_export(path, Path(d, "work"), allow_partial=True)
            self.assertFalse(result["complete"])
            self.assertEqual(result["rows"]["pages"], 1)

    def test_imports_result_contract_and_public_api_without_treating_award_as_completion(self):
        with tempfile.TemporaryDirectory() as d:
            records = [{"type": "metadata", "source": "https://www.tender.gov.mn"},
                       {"type": "page", "kind": "result_detail", "url": "https://www.tender.gov.mn/mn/result/detail/123",
                        "tables": [["Winner", "Public Company"]]},
                       {"type": "page", "kind": "contract_detail", "url": "https://www.tender.gov.mn/mn/contract/detail/456"},
                       {"type": "api_response", "url": "https://www.tender.gov.mn/api/invitation?page=1", "data": [{"id": 123}]},
                       {"type": "manifest", "records": 4, "complete": True}]
            path = Path(d, "data.jsonl")
            path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
            result = import_export(path, Path(d, "work"))
            self.assertFalse(result["complete"])
            self.assertEqual(result["rows"]["pages"], 2)
            self.assertEqual(result["rows"]["api_responses"], 1)

    def test_rejects_sensitive_or_foreign_urls(self):
        for url in ["https://example.com/data", "https://www.tender.gov.mn/api?token=secret"]:
            with self.assertRaises(ValueError):
                public_url(url)


if __name__ == "__main__":
    unittest.main()
