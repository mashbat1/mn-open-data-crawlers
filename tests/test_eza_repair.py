import gzip
import json
from pathlib import Path
import tempfile
import unittest
import pyarrow.parquet as pq
from ci import write_table
from crawlers.shilendans_eza import ROW_FILTERS, registers_from_parts, validate_snapshot


class EzaRepairTests(unittest.TestCase):
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
