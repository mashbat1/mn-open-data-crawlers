"""Re-fetch every failed form-month in a published bulk snapshot and stream the repaired snapshot to Releases."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from crawlers.core import Http
from crawlers.shilendans_common import REPORT, REPORTS, pages_all
from od_partitions import collect_partitions

REPO = 'mashbat1/gov-crawler'


def gh(*args):
    return subprocess.check_output(['gh', *args, '-R', REPO], text=True)


def replace_partitions(base, updates, target):
    original = pq.ParquetFile(base)
    count = removed = 0
    with pq.ParquetWriter(target, original.schema_arrow, compression='zstd', compression_level=9) as writer:
        for batch in original.iter_batches(batch_size=100000):
            table = pa.Table.from_batches([batch])
            keep = pa.array([True] * len(table))
            for fid, year, month in updates:
                match = pc.and_(pc.and_(pc.equal(table['form_id'], fid), pc.equal(table['year'], year)), pc.equal(table['month'], month))
                keep = pc.and_(keep, pc.invert(match))
            filtered = table.filter(keep)
            removed += len(table) - len(filtered)
            writer.write_table(filtered)
            count += len(filtered)
        for records in updates.values():
            for offset in range(0, len(records), 100000):
                normalized = []
                for record in records[offset:offset + 100000]:
                    row = dict(record)
                    for field in original.schema_arrow:
                        value = row.get(field.name)
                        if value is not None and pa.types.is_string(field.type) and not isinstance(value, str):
                            row[field.name] = json.dumps(value, ensure_ascii=False)
                    normalized.append(row)
                table = pa.Table.from_pylist(normalized, schema=original.schema_arrow)
                writer.write_table(table)
                count += len(table)
    return count, removed


def main():
    tag = sys.argv[1]
    root = Path('od-repair')
    base, output = root / 'base', root / 'output'
    base.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    gh('release', 'download', tag, '-p', 'manifest.json', '-D', str(base))
    manifest = json.loads((base / 'manifest.json').read_text(encoding='utf-8'))
    failed = manifest.get('failed_examples', {}).get('od', {})
    if len(failed) != manifest.get('failed_items', {}).get('od', 0):
        raise RuntimeError('Manifest does not enumerate all failed tasks; cannot assert repair completeness')
    if not failed:
        print('No failed bulk tasks in the selected snapshot')
        return
    updates, repair_proof, repair_tag = collect_partitions(tag, failed, root)
    if updates is None:
        print("Repair paused at the time budget; verified checkpoints will resume", flush=True)
        return
    gh('release', 'download', tag, '-p', '*.parquet', '-D', str(base))
    count, removed = replace_partitions(base / 'shilendans_od__od_rows.parquet', updates, output / 'shilendans_od__od_rows.parquet')
    import shutil
    shutil.copyfile(base / 'shilendans_od__od_forms.parquet', output / 'shilendans_od__od_forms.parquet')
    manifest.update(base_release=tag, repaired_tasks={k: len(updates[tuple(map(int, k.split(':')))]) for k in failed},
                    replaced_old_rows=removed, crawled=dt.datetime.now(dt.timezone.utc).date().isoformat(), failed_items={}, failed_examples={})
    manifest['server_reported']['od_rows'] = count
    manifest['coverage_scope'] = {'method': 'official bulk API partitioned by organisation directory',
                                  'repair_proof': repair_proof,
                                  'directory_scope_note': 'Every ID in the reconciled public organisation directory snapshot was queried.'}
    for table in manifest['tables']:
        path = output / f'shilendans_od__{table}.parquet'
        manifest['tables'][table] = {'rows': pq.ParquetFile(path).metadata.num_rows, 'bytes': path.stat().st_size}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    final_tag = 'shilendans_od-' + dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d-%H%M%S')
    gh('release', 'create', final_tag, *map(str, output.iterdir()), '--title', 'Shilendans bulk snapshot, failed months repaired',
       '--notes', f'Repaired all {len(failed)} failed form-months from {tag}. {count:,} total rows. No attachment downloads.', '--latest=false')
    assets = json.loads(gh('release', 'view', final_tag, '--json', 'assets'))['assets']
    sizes = {a['name']: a['size'] for a in assets}
    if any(sizes.get(p.name) != p.stat().st_size for p in output.iterdir()):
        raise RuntimeError('Uploaded snapshot sizes do not match')
    gh('release', 'delete', repair_tag, '--yes', '--cleanup-tag')
    print(f'Published and verified {final_tag}: {count} rows, no unresolved bulk tasks', flush=True)


if __name__ == '__main__':
    main()
