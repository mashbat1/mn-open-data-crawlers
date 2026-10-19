"""Legal-entity registry and procurement lists from the national open-data portal (opendata.gov.mn):
- the daily full dump of the legal-entity register (founders/shareholders, directors, beneficial owners)
- Ministry of Finance: suppliers barred from tenders, tender-complaint decisions
- the whole dataset catalogue
plus the liquidated / removed entity lists of the registration authority (opendata.burtgel.gov.mn)."""
import io
import json
import os
import re

import ijson
import openpyxl

from .core import Http, flat, log, strip_tags

OD = "https://opendata.gov.mn/api/v1"
BURTGEL = "https://opendata.burtgel.gov.mn"
REGISTRY_TITLE = "Хуулийн этгээдийн улсын бүртгэлийн нээлттэй өгөгдөл"
XLSX = {"barred_suppliers": "оролцох эрхээ хязгаарлуулсан", "tender_complaints": "гомдлын шийдвэрлэлт"}
KEYS = {"od_catalog": ["id"], "companies": ["Регистрийн дугаар"], "liquidated": ["reg_no"]}
http = Http(rate=2.0, timeout=600)


def catalog(ctx):
    page, items = 1, []
    while True:
        o = http.json(f"{OD}/front/search", method="POST",
                      json={"page": {"page": page, "page_size": 500, "sort": "created_at DESC"}})
        d = o["result"]["details"]
        items += d["items"]
        ctx.expect("od_catalog", d["total_rows"])
        if ctx.sample or page >= d["total_pages"]:
            return items
        page += 1


def newest_file(item, ext):
    files = [f for f in item.get("files") or [] if (f.get("ext") or "").lower() == ext]
    return max(files, key=lambda f: f.get("created_at") or "") if files else None


def header_names(path):
    """Field names from the dump's Header (a list of names, or of {name/title/...} objects)."""
    with open(path, "rb") as fh:
        try:
            header = next(ijson.items(fh, "Header", use_float=True), None)
        except ijson.IncompleteJSONError:
            header = None
    if isinstance(header, dict):
        header = next((v for v in header.values() if isinstance(v, list)), list(header))
    out = []
    for h in header or []:
        if isinstance(h, dict):
            h = next((h[k] for k in ("name", "field", "key", "title", "label", "code") if k in h), json.dumps(h, ensure_ascii=False))
        out.append(str(h))
    return out


def crawl(ctx):
    items = catalog(ctx)
    for it in items:
        ctx.write("od_catalog", flat(it))
    log(f"catalogue: {len(items)} datasets")

    # registry dump (~460 MB JSON), streamed
    reg = [it for it in items if REGISTRY_TITLE in (it.get("name") or "")]
    if not reg and ctx.sample:
        o = http.json(f"{OD}/front/search", method="POST",
                      json={"page": {"page": 1, "page_size": 50, "sort": "created_at DESC"}, "query": REGISTRY_TITLE})
        reg = [it for it in o["result"]["details"]["items"] if REGISTRY_TITLE in (it.get("name") or "")]
    f = newest_file(max(reg, key=lambda it: it.get("updated_at") or ""), "json") if reg else None
    if not f:
        raise RuntimeError("registry dataset not found in the catalogue")
    path = os.path.join(ctx.workdir, "registry.json")
    with http.get(f"{OD}/media/{f['path']}", stream=True, timeout=1800) as r, open(path, "wb") as out:
        for chunk in r.iter_content(1 << 20):
            out.write(chunk)
            if ctx.sample and out.tell() > 5_000_000:
                break
    size = os.path.getsize(path)
    if not ctx.sample and f.get("size") and size != f["size"]:
        raise RuntimeError(f"registry download incomplete: {size} of {f['size']} bytes")
    n = 0
    names = header_names(path)
    log(f"registry header: {len(names)} fields: {names}")
    try:
        with open(path, "rb") as fh:
            for rec in ijson.items(fh, "Data.item", use_float=True):
                if isinstance(rec, list):  # rows are arrays; column names come from the Header
                    rec = {(names[i] if i < len(names) else f"c{i}"): v for i, v in enumerate(rec)}
                ctx.write("companies", flat(rec))
                n += 1
                if ctx.sample and n >= 3:
                    break
    except ijson.IncompleteJSONError:
        if not ctx.sample:
            raise
    os.remove(path)
    ctx.expect("companies_file_bytes", size)
    ctx.state["registry_file"] = {"path": f["path"], "created_at": f.get("created_at"), "rows": n}
    log(f"registry: {n} legal entities")

    # Ministry of Finance spreadsheets: every published file (complaints come as monthly files), rows deduplicated
    XLSX_ALL = {"barred_suppliers": r"оролцох эрхээ хязгаарлуулсан", "tender_complaints": r"гомдлын"}
    for table, needle in XLSX_ALL.items():
        files = []
        for it in items:
            name = (it.get("name") or "").lower()
            if re.search(needle, name) and ("тендер" in name or table == "barred_suppliers"):
                files += [(it.get("name"), f) for f in it.get("files") or [] if (f.get("ext") or "").lower() in ("xlsx", "xls")]
        seen = set()
        for title, fx in files:
            try:
                wb = openpyxl.load_workbook(io.BytesIO(http.get(f"{OD}/media/{fx['path']}").content), read_only=True, data_only=True)
            except Exception as e:  # noqa: BLE001 - one unreadable file must not stop the rest
                log(f"{table}: {fx.get('path')} unreadable ({type(e).__name__})")
                continue
            for ws in wb.worksheets:
                rows = list(ws.iter_rows(values_only=True))
                hdr_i = next((i for i, r in enumerate(rows) if sum(1 for c in r if c not in (None, "")) >= 3), None)
                if hdr_i is None:
                    continue
                hdr = [str(c or f"col{j}").strip() for j, c in enumerate(rows[hdr_i])]
                for r in rows[hdr_i + 1:]:
                    if not any(c not in (None, "") for c in r):
                        continue
                    rec = {hdr[j]: (str(c) if c is not None else None) for j, c in enumerate(r) if j < len(hdr)}
                    key = json.dumps({k: v for k, v in rec.items() if k not in ("№", "No", "Д/д")}, ensure_ascii=False, sort_keys=True)
                    if key in seen:
                        continue
                    seen.add(key)
                    ctx.write(table, {"source_file": title, "sheet": ws.title, **rec})
        ctx.expect(f"{table}_files", len(files))
        log(f"{table}: {len(files)} files, {len(seen)} unique rows")

    # liquidated entities (DataTables JSON)
    start, total = 0, None
    while total is None or start < total:
        o = http.json(f"{BURTGEL}/les/table", params={"draw": 1, "start": start, "length": 1000})
        total = o.get("recordsTotal") or 0
        ctx.expect("liquidated", total)
        for row in o.get("data") or []:
            vals = [strip_tags(str(c)) for c in row]
            ctx.write("liquidated", {"reg_no": vals[0] if vals else None, "name": vals[1] if len(vals) > 1 else None,
                                     "legal_form": vals[2] if len(vals) > 2 else None,
                                     "liquidated_on": vals[3] if len(vals) > 3 else None})
        if ctx.sample:
            break
        start += 1000
    # removed from the register (HTML table, ?page=N)
    page, prev = 1, None
    while True:
        html = http.get(f"{BURTGEL}/lescanceled", params={"text": "", "page": page}).text
        rows = [[strip_tags(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)]
        rows = [r for r in rows if r]
        if not rows or rows == prev or page > 2000:
            break
        for r in rows:
            ctx.write("removed", {f"c{j}": v for j, v in enumerate(r)})
        if ctx.sample:
            break
        prev, page = rows, page + 1
    log(f"removed: {page - 1} pages")
    return True
