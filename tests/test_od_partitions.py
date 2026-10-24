from pathlib import Path
import tempfile
import unittest
from od_partitions import cached_export


class PartitionTests(unittest.TestCase):
    def test_split_retry_is_disjoint_preserves_duplicate_rows_and_reuses_cache(self):
        calls = []
        class Client:
            def json(self, url, method, json):
                ids = json["orgs"]
                calls.append(ids)
                if len(ids) > 1:
                    raise RuntimeError("large export timed out")
                return [{"org": ids[0]}, {"org": ids[0]}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "batch.json.gz")
            rows = cached_export(Client(), {"months": [3]}, [1, 2], path)
            self.assertEqual([r["org"] for r in rows], [1, 1, 2, 2])
            self.assertEqual(calls, [[1, 2], [1], [2]])
            self.assertEqual(cached_export(Client(), {}, [1, 2], path), rows)
            self.assertEqual(len(calls), 3)
