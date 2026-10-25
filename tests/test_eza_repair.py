import gzip
import json
from pathlib import Path
import tempfile
import unittest
import pyarrow.parquet as pq
from ci import write_table
from crawlers.shilendans_eza import ROW_FILTERS, registers_from_parts, validate_snapshot, row_filters, reconciled_period
import hashlib


class EzaRepairTests(unittest.TestCase):
    def test_reconciled_month_excludes_obsolete_supplier_identity(self):
        keep = row_filters({"reconciled_period_keys": {"2026-09": [["123", "Current name"]]}})["eza_suppliers"]
        self.assertTrue(keep({"period": "2026-09", "register": "123", "name": "Current name"}))
        self.assertFalse(keep({"period": "2026-09", "register": "123", "name": "Obsolete name"}))
        self.assertTrue(keep({"period": "2026-08", "register": "123", "name": "Any name"}))

    def test_reconciliation_requires_both_counts_and_matching_rows_digest(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d, "monthly_reconciliation", "2026-09")
            folder.mkdir(parents=True)
            encoded = json.dumps([{"register": "123", "name": "Name", "count": 2, "amount": 100}]).encode()
            (folder / "reconciled_rows.json").write_bytes(encoded)
            proof = {"period": "2026-09", "verified": True, "expected": 1,
                     "reported_transaction_total": 2, "terminal_short_page_verified": True,
                     "baseline_mismatches": 0, "rows_sha256": hashlib.sha256(encoded).hexdigest()}
            (folder / "proof.json").write_text(json.dumps(proof))
            self.assertEqual(len(reconciled_period(d, "2026-09")[0]), 1)
            proof["reported_transaction_total"] = 3
            (folder / "proof.json").write_text(json.dumps(proof))
            with self.assertRaisesRegex(RuntimeError, "invalid"):
                reconciled_period(d, "2026-09")
    def test_input_union_includes_months_missing_from_old_annual_register_lists(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "suppliers.gz")
            with gzip.open(path, "wt", encoding="utf-8") as out:
                for period, reg in [("2025", "legacy"), ("2026-01", "one"), ("2026-05", "two")]:
                    out.write(json.dumps({"period": period, "register": reg}) + "\n")
            self.assertEqual(registers_from_parts([path]), {"2026": ["one", "two"]})

    def test_unreconciled_supplier_inputs_block_final_publication(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RuntimeError, "rebuilt"):
                validate_snapshot({}, d)
    def test_unreliable_legacy_annual_rows_do_not_contaminate_monthly_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            part, target = Path(d, "part.gz"), Path(d, "result.parquet")
            with gzip.open(part, "wt", encoding="utf-8") as out:
                for period, amount in [("2026", 999), ("2026-05", 100)]:
                    out.write(json.dumps({"period": period, "register": "1234567", "amount": amount}) + "\n")
            n = write_table([str(part)], ["period", "register"], target, keep=ROW_FILTERS["eza_suppliers"])
            self.assertEqual(n, 1)
            self.assertEqual(pq.read_table(target).to_pylist()[0]["amount"], 100)
