"""Publish evidence-based collection status to the PRIVATE data repo; never runs analysis or deploys a site."""
import datetime as dt
import json
from pathlib import Path
import subprocess

REPO = 'mashbat1/gov-crawler'
SOURCES = ['parliament', 'xacxom', 'legalinfo', 'shuukh', 'companies', 'registry', 'sanctions', 'egazar',
           'iaac', 'shilendans_eza', 'shilendans_od', 'shilendans_reports', 'audit', 'eiti', 'cabinet', 'ebarimt', 'tender']


def gh(*args, check=True):
    r = subprocess.run(['gh', *args, '-R', REPO], text=True, capture_output=True)
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return r


def assess(manifest):
    failed = {k: v for k, v in manifest.get('failed_items', {}).items() if v}
    mismatches = {}
    for name, table in manifest.get('tables', {}).items():
        expected = manifest.get('server_reported', {}).get(name)
        if isinstance(expected, int) and table['rows'] != expected:
            mismatches[name] = {'expected': expected, 'stored': table['rows']}
    return {'status': 'needs_attention' if failed or mismatches else 'published',
            'tables': {k: v['rows'] for k, v in manifest.get('tables', {}).items()},
            'failed_items': failed, 'count_mismatches': mismatches,
            'coverage_scope': manifest.get('coverage_scope'), 'crawled': manifest.get('crawled')}


def main():
    releases = json.loads(gh('release', 'list', '--limit', '100', '--json', 'tagName,createdAt,isPrerelease').stdout)
    tags = {r['tagName'] for r in releases}
    folder = Path('work_coverage')
    folder.mkdir(exist_ok=True)
    result = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'scope_note': 'Publication status within each configured crawler scope, not a guarantee about unpublished data.', 'sources': {}}
    for source in SOURCES:
        candidates = [r for r in releases if not r['isPrerelease'] and r['tagName'].startswith(source + '-')]
        entry = {'status': 'missing'}
        if candidates:
            tag = max(candidates, key=lambda r: r['createdAt'])['tagName']
            target = folder / source
            target.mkdir(exist_ok=True)
            download = gh('release', 'download', tag, '-p', 'manifest.json', '-D', str(target), '--clobber', check=False)
            if download.returncode:
                entry = {'status': 'unverified', 'error': 'manifest could not be downloaded'}
            else:
                entry = assess(json.loads((target / 'manifest.json').read_text(encoding='utf-8')))
            entry['release'] = tag
        wip = 'wip-' + source
        if wip in tags:
            entry['status'] = 'in_progress'
            entry['partial_release'] = wip
            target = folder / (source + '-partial')
            target.mkdir(exist_ok=True)
            download = gh('release', 'download', wip, '-p', 'state.json', '-D', str(target), '--clobber', check=False)
            if download.returncode == 0:
                state = json.loads((target / 'state.json').read_text(encoding='utf-8'))
                entry['completed_stage_items'] = {k: len(v) for k, v in state.get('done', {}).items()}
                entry['pending_errors'] = {k: len(v) for k, v in state.get('errors', {}).items() if v}
                entry['last_crash'] = state.get('last_crash')
        result['sources'][source] = entry
    result['all_sources_published'] = all(s['status'] == 'published' for s in result['sources'].values())
    target = folder / 'coverage.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if gh('release', 'view', 'crawl-status', check=False).returncode:
        gh('release', 'create', 'crawl-status', '--title', 'Data collection status', '--notes',
           'Collection status and unresolved counts. No attachments, analysis or deployment.', '--prerelease', '--latest=false')
    gh('release', 'upload', 'crawl-status', str(target), '--clobber')
    print(json.dumps({'all_sources_published': result['all_sources_published'],
                      'sources': {s: e['status'] for s, e in result['sources'].items()}}))


if __name__ == '__main__':
    main()
