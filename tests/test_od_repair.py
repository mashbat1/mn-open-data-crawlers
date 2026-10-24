import tempfile
from pathlib import Path
import unittest
import pyarrow as pa
import pyarrow.parquet as pq
from repair_od import replace_partitions


class RepairTests(unittest.TestCase):
    def test_replaces_only_target_partitions_and_preserves_empty_repair(self):
        with tempfile.TemporaryDirectory() as d:
            base, target = Path(d, 'base.parquet'), Path(d, 'repaired.parquet')
            pq.write_table(pa.Table.from_pylist([
                {'form_id': 1, 'year': 2023, 'month': 1, 'data': 'old'},
                {'form_id': 2, 'year': 2023, 'month': 1, 'data': 'keep'},
                {'form_id': 3, 'year': 2023, 'month': 1, 'data': 'remove-empty'}]), base)
            count, removed = replace_partitions(base, {
                (1, 2023, 1): [{'form_id': 1, 'year': 2023, 'month': 1, 'data': 'new'}],
                (3, 2023, 1): []}, target)
            self.assertEqual((count, removed), (2, 2))
            self.assertEqual({r['data'] for r in pq.read_table(target).to_pylist()}, {'keep', 'new'})
            self.assertEqual(pq.ParquetFile(base).metadata.num_rows, 3)
