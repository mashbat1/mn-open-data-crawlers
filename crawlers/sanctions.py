"""Watch lists: OpenSanctions (CC BY-NC 4.0 — non-commercial use), World Bank debarments (via OpenSanctions),
ADB sanctions (official API, CC BY 3.0 IGO). Large OpenSanctions files are streamed and filtered to Mongolia."""
import csv
import io

from .core import Http, flat, log

OS_INDEX = "https://data.opensanctions.org/datasets/latest/index.json"
FULL = ("mn_parliament", "worldbank_debarred", "adb_sanctions")
MN_FILTERED = ("default",)        # 'default' already contains peps + sanctions; keep only rows linked to Mongolia
ADB = "https://apim.adb.org/sanctions/lists/v1/published-list"
KEYS = {"opensanctions": ["dataset", "id"], "adb": ["_row"]}
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
            log(f"{name}: targets.simple.csv not found")
            continue
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
    offset, total, i = 0, None, 0
    while total is None or offset < total:
        o = http.json(ADB, params={"size": 100, "offset": offset})
        total = (o.get("meta") or {}).get("totalItems") or 0
        ctx.expect("adb", total)
        rows = first_list(o)
        if not rows:
            break
        for r in rows:
            ctx.write("adb", {"_row": i, **flat(r)})
            i += 1
        if ctx.sample:
            break
        offset += 100
    log(f"adb: {i} of {total}")
    return True
