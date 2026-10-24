"""Prepare lookup input on Actions; transfer register numbers only, no registry dump, to a local-only crawl."""
import json
from pathlib import Path
import re
import subprocess

import pyarrow.parquet as pq
import requests

REPO = 'mashbat1/gov-crawler'


def gh(*args):
    return subprocess.check_output(['gh', *args, '-R', REPO], text=True)


def main():
    releases = json.loads(gh('release', 'list', '--limit', '100', '--json', 'tagName,createdAt'))
    tag = max((r for r in releases if r['tagName'].startswith(('registry-', 'companies-'))), key=lambda r: r['createdAt'])['tagName']
    prefix = 'registry' if tag.startswith('registry-') else 'companies'
    folder = Path('prepared-inputs')
    folder.mkdir(exist_ok=True)
    gh('release', 'download', tag, '-p', f'{prefix}__companies.parquet', '-D', str(folder))
    parquet = pq.ParquetFile(folder / f'{prefix}__companies.parquet')
    # The source's header is shifted: first column is the actual company register.
    column = parquet.schema_arrow.names[0]
    registers = sorted({str(v).strip() for batch in parquet.iter_batches(columns=[column])
                        for v in batch.column(0).to_pylist() if re.fullmatch(r'\d{7}', str(v).strip())})
    if not registers:
        raise RuntimeError('No valid company registers in the published registry')
    target = folder / 'ebarimt_registers.json'
    target.write_text(json.dumps(registers), encoding='utf-8')
    probe = requests.get('https://info.ebarimt.mn/rest/merchant/info', params={'regno': registers[0]}, timeout=30)
    network = {'status': probe.status_code, 'json': False}
    try:
        network['json'] = isinstance(probe.json(), dict)
    except ValueError:
        pass
    manifest = folder / 'input_manifest.json'
    manifest.write_text(json.dumps({'source_release': tag, 'registers': len(registers), 'ebarimt_cloud_probe': network}), encoding='utf-8')
    release = 'lookup-inputs-' + __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%d-%H%M%S')
    gh('release', 'create', release, str(target), str(manifest), '--title', 'Company registers for public lookups',
       '--notes', f'{len(registers)} unique company registers from {tag}. Cloud connectivity: {network}.', '--latest=false')
    print(json.dumps({'tag': release, 'registers': len(registers), 'cloud_probe': network}))


if __name__ == '__main__':
    main()
