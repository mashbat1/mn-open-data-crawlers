"""opendata.gov.mn - the national open-data portal: every publishing organisation, dataset series, dataset and
resource file, with resource files under 50 MB parsed into rows.

The portal is not CKAN (/api/3/action/* serves the SPA shell); its Nuxt front end uses a JSON API under
/api/v1/front/* (POST) and serves files from /api/v1/media/<path>. robots.txt is absent (the SPA shell is
returned), so the crawl runs at 1 request/s.

Model: organisation -> dataset series ("data", uuid; from /front/org/view) -> dataset ("detail", int id; the
catalogue search) -> resource files (int id; csv/xlsx/json/xml). Spreadsheets are messy, presentation-style
tables, so rows keep their cells positionally (`data` is a JSON list for csv/xlsx/SpreadsheetML, a JSON object
for json/xml records) together with the dataset and file ids.

Six registration-authority series republish the same files every day (~540 copies each, 22 GB of the 26 GB
under 50 MB). For a series that repeats a dataset title + file name, only the newest copy is parsed; older
copies stay listed in od_resources with parse_status "superseded_snapshot" and their download URL.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import xml.etree.ElementTree as ET
from urllib import robotparser

import openpyxl

from .core import USER_AGENT, Http, NotFound, flat, log

BASE = "https://opendata.gov.mn"
API = BASE + "/api/v1"
MAX_BYTES = 50 * 1024 * 1024
PAGE = 500
KEYS = {"od_orgs": ["reg_num"], "od_series": ["org_reg_num", "uuid"], "od_datasets": ["id"],
        "od_resources": ["file_id"], "od_files": ["file_id"], "od_rows": ["file_id", "sheet", "row"]}
http = Http(rate=1.0, timeout=300, retries=4)
csv.field_size_limit(1 << 26)


# ---------------------------------------------------------------- robots
def obey_robots(client, base, paths):
    """Fetch robots.txt first; refuse disallowed paths and adopt Crawl-delay (never faster than 1 req/s)."""
    try:
        r = client.get(base + "/robots.txt")
        text = r.text if "html" not in (r.headers.get("content-type") or "") else ""
    except NotFound:
        text = ""
    rp = robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    blocked = [p for p in paths if not rp.can_fetch(USER_AGENT, base + p)]
    if blocked:
        raise RuntimeError(f"robots.txt of {base} disallows {blocked}")
    delay = rp.crawl_delay(USER_AGENT)
    client.interval = max(client.interval, float(delay or 0), 1.0)
    return text


# ---------------------------------------------------------------- portal API
def api(client, endpoint, body):
    o = client.json(f"{API}/front/{endpoint}", method="POST", json=body)
    if not isinstance(o, dict) or o.get("code") != 200:
        raise RuntimeError(f"{endpoint}: {str(o)[:200]}")
    return o["result"]


def search_all(client, sample=False, **filters):
    """Every dataset matching `filters`, oldest first so pages do not shift while new datasets are added."""
    page, items, total = 1, {}, None
    while True:
        res = api(client, "search", {"page": {"page": page, "page_size": 50 if sample else PAGE,
                                              "sort": "created_at ASC"}, **filters})
        d = res["details"]
        total = d["total_rows"]
        for it in d["items"]:
            items[it["id"]] = it
        if sample or page >= d["total_pages"]:
            break
        page += 1
    if not sample and len(items) < total:
        raise RuntimeError(f"catalogue returned {len(items)} unique datasets, server reports {total}")
    return [items[k] for k in sorted(items)], total


def media_url(path):
    return f"{API}/media/{path}"


def download(client, path, limit=MAX_BYTES):
    """Bytes of a resource file, or None when it turns out to be larger than `limit`."""
    with client.get(media_url(path), stream=True) as r:
        buf = io.BytesIO()
        for chunk in r.iter_content(1 << 20):
            buf.write(chunk)
            if buf.tell() > limit:
                return None
        return buf.getvalue()


# ---------------------------------------------------------------- file parsing
def _cell(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _trim(cells):
    cells = [_cell(c) for c in cells]
    while cells and cells[-1] in (None, ""):
        cells.pop()
    return cells


def sniff(data, ext):
    head = data[:16].lstrip(b"\xef\xbb\xbf \t\r\n")
    if data[:2] == b"PK":
        return "xlsx"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls"
    if head[:1] == b"<":
        return "xml"
    if head[:1] in (b"[", b"{"):
        return "json"
    return (ext or "csv").lower()


def decode(data):
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251")


def rows_xlsx(data):
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            for i, row in enumerate(ws.iter_rows(values_only=True), 1):
                cells = _trim(row)
                if cells:
                    yield ws.title, i, cells
    finally:
        wb.close()


def rows_xls(data):
    import xlrd  # legacy Excel 97-2003 workbooks
    wb = xlrd.open_workbook(file_contents=data)
    for ws in wb.sheets():
        for i in range(ws.nrows):
            cells = []
            for c in ws.row(i):
                v = c.value
                if c.ctype == xlrd.XL_CELL_DATE:
                    v = xlrd.xldate.xldate_as_datetime(v, wb.datemode)
                cells.append(v)
            cells = _trim(cells)
            if cells:
                yield ws.name, i + 1, cells


def rows_csv(data):
    text = decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    for i, row in enumerate(csv.reader(io.StringIO(text, newline=""), dialect), 1):
        cells = _trim(row)
        if cells:
            yield "csv", i, cells


def rows_json(data):
    o = json.loads(decode(data))
    if isinstance(o, list):
        for i, rec in enumerate(o, 1):
            yield "json", i, rec
        return
    if not isinstance(o, dict):
        yield "json", 1, o
        return
    lists = {k: v for k, v in o.items() if isinstance(v, list)}
    rest = {k: v for k, v in o.items() if k not in lists}
    if rest:
        yield "_root", 0, rest
    for k, v in lists.items():
        for i, rec in enumerate(v, 1):
            yield k, i, rec


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _elem(e):
    kids = list(e)
    attrs = {"@" + _local(k): v for k, v in e.attrib.items()}
    if not kids:
        text = (e.text or "").strip()
        return {**attrs, "_text": text} if attrs else text
    out, repeated = dict(attrs), set()
    for k in kids:
        name, val = _local(k.tag), _elem(k)
        if name not in out:
            out[name] = val
            continue
        if name not in repeated:
            out[name], _ = [out[name]], repeated.add(name)
        out[name].append(val)
    return out


def rows_xml(data):
    root = ET.fromstring(data)
    if _local(root.tag) == "Workbook":   # Excel 2003 SpreadsheetML
        for ws in (c for c in root if _local(c.tag) == "Worksheet"):
            name = next((v for k, v in ws.attrib.items() if _local(k) == "Name"), "Sheet")
            for table in (c for c in ws if _local(c.tag) == "Table"):
                r = 0
                for row in (c for c in table if _local(c.tag) == "Row"):
                    idx = next((v for k, v in row.attrib.items() if _local(k) == "Index"), None)
                    r = int(idx) if idx else r + 1
                    cells, col = [], 0
                    for cell in (c for c in row if _local(c.tag) == "Cell"):
                        cidx = next((v for k, v in cell.attrib.items() if _local(k) == "Index"), None)
                        col = int(cidx) if cidx else col + 1
                        cells += [None] * (col - 1 - len(cells))
                        d = next((c for c in cell if _local(c.tag) == "Data"), None)
                        cells.append("".join(d.itertext()).strip() if d is not None else None)
                    cells = _trim(cells)
                    if cells:
                        yield name, r, cells
        return
    for i, child in enumerate(root, 1):
        yield _local(child.tag), i, _elem(child)


PARSERS = {"xlsx": rows_xlsx, "xls": rows_xls, "csv": rows_csv, "json": rows_json, "xml": rows_xml}


def parse_file(data, ext):
    """(format, iterator of (sheet, row_number, value)); raises ValueError for unsupported formats."""
    fmt = sniff(data, ext)
    if fmt not in PARSERS:
        raise ValueError(f"unsupported format {fmt}")
    return fmt, PARSERS[fmt](data)


def fetch_and_parse(ctx, client, f, rows_table, files_table, extra=None):
    """Download one resource and write its rows; parse problems are recorded, network errors raise."""
    stat = {"file_id": f["file_id"], "detail_id": f["detail_id"], "url": media_url(f["path"]),
            "declared_size": f.get("size"), **(extra or {})}
    try:
        data = download(client, f["path"])
    except NotFound:  # listed in the catalogue but the file is gone from the server
        ctx.write(files_table, {**stat, "error": "missing on server (HTTP 404)", "rows": 0})
        return
    if data is None:
        ctx.write(files_table, {**stat, "error": f"larger than {MAX_BYTES} bytes on download", "rows": 0})
        return
    n, sheets, err, fmt = 0, set(), None, None
    try:
        fmt, rows = parse_file(data, f.get("ext"))
        for sheet, rown, value in rows:
            sheets.add(sheet)
            ctx.write(rows_table, {"detail_id": f["detail_id"], "file_id": f["file_id"], "sheet": sheet,
                                   "row": rown, "data": json.dumps(value, ensure_ascii=False, default=str)})
            n += 1
    except Exception as e:  # noqa: BLE001 - malformed files are reported, not retried forever
        err = f"{type(e).__name__}: {str(e)[:200]}" + (f" (after {n} rows)" if n else "")
    ctx.write(files_table, {**stat, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                            "format": fmt, "rows": n, "sheets": json.dumps(sorted(sheets), ensure_ascii=False),
                            "error": err})


# ---------------------------------------------------------------- crawl
def plan_files(items):
    """Resource rows for every file, and the list of files to parse."""
    newest = {}
    for it in items:
        for f in it.get("files") or []:
            k = (it["data"]["uuid"], (it.get("name") or "").strip().lower(),
                 (f.get("name") or "").strip().lower(), (f.get("ext") or "").lower())
            cur = newest.get(k)
            if cur is None or (f.get("created_at") or "", f["id"]) > (cur.get("created_at") or "", cur["id"]):
                newest[k] = f
    keep = {f["id"] for f in newest.values()}
    resources, plan, seen_path = [], [], {}
    for it in items:
        for f in it.get("files") or []:
            if f["id"] not in keep:
                status = "superseded_snapshot"
            elif (f.get("size") or 0) >= MAX_BYTES:
                status = "over_50MB"
            elif f["path"] in seen_path:
                status = f"duplicate_of:{seen_path[f['path']]}"
            else:
                status = "parse"
                seen_path[f["path"]] = f["id"]
                plan.append({"file_id": f["id"], "detail_id": it["id"], "path": f["path"],
                             "ext": (f.get("ext") or "").lower(), "size": f.get("size")})
            resources.append({"file_id": f["id"], "detail_id": it["id"], "series_uuid": it["data"]["uuid"],
                              "org_reg_num": it["org"]["reg_num"], "url": media_url(f["path"]),
                              "parse_status": status, **{k: v for k, v in flat(f).items() if k != "id"}})
    return resources, plan


def dataset_row(it):
    row = flat({k: v for k, v in it.items() if k != "files"})
    row["file_ids"] = json.dumps([f["id"] for f in it.get("files") or []])
    row["n_files"] = len(it.get("files") or [])
    return row


def crawl(ctx):
    st = ctx.state
    obey_robots(http, BASE, ["/api/v1/front/search", "/api/v1/media/x"])
    complete = True
    if not ctx.stage_done("catalog"):
        items, total = search_all(http, ctx.sample)
        ctx.expect("od_datasets", total)
        for it in items:
            ctx.write("od_datasets", dataset_row(it))
        resources, plan = plan_files(items)
        for r in resources:
            ctx.write("od_resources", r)
        ctx.expect("od_resources", len(resources))
        ctx.expect("od_files_to_parse", len(plan))
        if ctx.sample:   # one file of each format
            plan = list({p["ext"]: p for p in sorted(plan, key=lambda p: -(p["size"] or 0))}.values())
        st["plan"] = plan
        st["catalog_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        log(f"catalogue: {len(items)} datasets, {len(resources)} files, {len(plan)} to parse")
        orgs = api(http, "org/page/list", {"page": {"page": 1, "page_size": 1000}, "name": ""})
        st["orgs"] = [o["reg_num"] for o in orgs["items"]]
        ctx.expect("od_orgs", orgs["total_rows"])
        if len(set(st["orgs"])) < orgs["total_rows"]:
            raise RuntimeError(f"org list returned {len(st['orgs'])} of {orgs['total_rows']}")
        ctx.mark_stage("catalog")

    def org(reg):
        o = api(http, "org/view", {"reg_num": reg})
        series = o.pop("data_list", None) or []
        o.pop("token", None)
        ctx.write("od_orgs", flat(o))
        for s in series:
            ctx.write("od_series", {"org_reg_num": reg, **flat(s)})

    complete &= ctx.run_items("orgs", sorted(set(st["orgs"])), org, workers=1)
    files = {str(p["file_id"]): p for p in st["plan"]}
    complete &= ctx.run_items("files", list(files), lambda k: fetch_and_parse(ctx, http, files[k], "od_rows", "od_files"),
                              workers=2)
    st["coverage_scope"] = {"catalogue_snapshot": st.get("catalog_at"), "files_parsed": len(files),
                            "rule": "files >= 50 MB and superseded daily snapshots are listed, not parsed"}
    return complete
