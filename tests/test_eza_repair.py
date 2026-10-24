import gzip
import json
from pathlib import Path
import tempfile
import unittest
import pyarrow.parquet as pq
from ci import write_table
from crawlers.shilendans_eza import ROW_FILTERS


class EzaRepairTests(unittest.TestCase):
    def test_unreliable_legacy_annual_rows_do_not_contaminate_monthly_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            part, target = Path(d, "part.gz"), Path(d, "result.parquet")
            with gzip.open(part, "wt", encoding="utf-8") as out:
                for period, amount in [("2026", 999), ("2026-05", 100)]:
                    out.write(json.dumps({"period": period, "register": "1234567", "amount": amount}) + "\n")
            n = write_table([str(part)], ["period", "register"], target, keep=ROW_FILTERS["eza_suppliers"])
            self.assertEqual(n, 1)
            self.assertEqual(pq.read_table(target).to_pylist()[0]["amount"], 100)
