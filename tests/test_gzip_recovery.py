import gzip
import json
from pathlib import Path
import tempfile
import unittest
from repair_gzip import recover


class GzipRecoveryTests(unittest.TestCase):
    def test_recovers_complete_rows_but_reports_the_incomplete_tail(self):
        with tempfile.TemporaryDirectory() as d:
            original, target = Path(d, "broken.gz"), Path(d, "recovered.gz")
            raw = b'{"period":"2026-09","id":1}\n{"period":"2026-09","id":'
            original.write_bytes(gzip.compress(raw)[:-8])
            proof = recover(original, target)
            self.assertEqual(proof["rows"], 1)
            self.assertGreater(proof["discarded_partial_tail_bytes"], 0)
            self.assertFalse(proof["original_stream_complete"])
            with gzip.open(target, "rt") as inp:
                self.assertEqual([json.loads(line)["id"] for line in inp], [1])
