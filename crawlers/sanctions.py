"""Watch lists: OpenSanctions (CC BY-NC 4.0 — non-commercial use), World Bank debarments (via OpenSanctions),
ADB sanctions (official API, CC BY 3.0 IGO). Large OpenSanctions files are streamed and filtered to Mongolia."""
import csv
import json

from .core import Http, flat, log

OS_INDEX = "https://data.opensanctions.org/datasets/latest/index.json"
FULL = ("mn_parliament", "worldbank_debarred", "adb_sanctions")
MN_FILTERED = ("default",)        # 'default' already contains peps + sanctions; keep only rows linked to Mongolia
ADB = "https://apim.adb.org/sanctions/lists/v1/published-list"
KEYS = {"opensanctions": ["dataset", "id"], "adb": ["source_row"]}
http = Http(rate=2.0, timeout=1800)
csv.field_size_limit(1 << 26)


def first_list(o):
    if isinstance(o, list):
        return o
    for k, v in o.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def crawl(ctx):
    idx = http.json(OS_INDEX)
    by_name = {d["name"]: d for d in idx["datasets"]}
    for name in FULL + MN_FILTERED:
        d = by_name.get(name)
        res = next((r for r in (d or {}).get("resources", []) if r["name"] == "targets.simple.csv"), None)
        if not res:
            raise RuntimeError(f"{name}: required targets.simple.csv not found")
        n = kept = 0
        with http.get(res["url"], stream=True) as r:
            r.encoding = "utf-8"
            lines = (line for line in r.iter_lines(decode_unicode=True))
            for row in csv.DictReader(lines):
                n += 1
                if name in MN_FILTERED and "mn" not in (row.get("countries") or "").split(";"):
                    continue
                ctx.write("opensanctions", {"dataset": name, **row})
                kept += 1
                if ctx.sample and kept >= 3:
                    break
        ctx.expect(f"opensanctions:{name}", kept)
        ctx.state.setdefault("versions", {})[name] = d.get("last_export")
        log(f"{name}: {kept} kept of {n}")
    # The published API caps size at 100. Preserve source entries even when
    # several entries share an entity ID; otherwise rows disappear at finalize.
    size = 100
    o = http.json(ADB, params={"size": size, "offset": 0})
    if not isinstance(o, dict) or not isinstance((o.get("meta") or {}).get("totalItems"), int):
        raise RuntimeError("ADB did not expose a valid total")
    total = o['meta']['totalItems']
    ctx.expect("adb", total)
    offset, ids = 0, set()
    while offset < total:
        batch = first_list(o)
        if not batch:
            raise RuntimeError(f"ADB empty page at {offset} before {total}")
        for i, row in enumerate(batch):
            rid = str(row.get('id') or json.dumps(row, sort_keys=True))
            ids.add(rid)
            ctx.write('adb', {'_id': rid, 'source_row': offset + i, **flat(row)})
            if ctx.sample and i >= 2:
                return True
        offset += len(batch)
        if offset >= total:
            break
        o = http.json(ADB, params={'size': size, 'offset': offset})
        if (o.get('meta') or {}).get('totalItems') != total:
            raise RuntimeError('ADB total changed during collection')
    if offset != total:
        raise RuntimeError(f'ADB received {offset} entries vs {total} reported')
    ctx.state['adb_unique_ids'] = len(ids)
    ctx.state['adb_duplicate_id_entries'] = offset - len(ids)
    ctx.state['coverage_scope'] = {'adb': {'reported_entries': total, 'collected_entries': offset,
                                          'distinct_entity_ids': len(ids), 'duplicate_id_entries': offset - len(ids)}}
    log(f'adb: {offset} source entries, {len(ids)} distinct entity IDs, {total} reported')
    return True
