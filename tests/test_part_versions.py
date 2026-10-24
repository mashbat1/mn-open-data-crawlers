import gzip
import json
from pathlib import Path
import tempfile
import unittest
import pyarrow.parquet as pq
from ci import write_table


class PartVersionsTests(unittest.TestCase):
    def test_newer_local_repair_overrides_older_cloud_part(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "rows.40059167824.001.jsonl.gz"
            new = root / "rows.20261024211426.001.jsonl.gz"
            for path, amount in [(old, 1), (new, 2)]:
                with gzip.open(path, "wt", encoding="utf-8") as target:
                    target.write(json.dumps({"id": 1, "amount": amount}) + "\n")
            output = root / "result.parquet"
            self.assertEqual(write_table([str(old), str(new)], ["id"], output,
                                        file_versions={old.name: "2026-10-23T00:00:00Z", new.name: "2026-10-24T00:00:00Z"}), 1)
            self.assertEqual(pq.read_table(output).to_pylist(), [{"id": 1, "amount": 2}])
