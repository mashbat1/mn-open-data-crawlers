"""Shared pieces for the Shilendans (shilendans.gov.mn) gap-filling crawlers: endpoints and Spring paging."""
from .core import log
import json
from pathlib import Path

API = "https://back.shilendans.gov.mn/proxy"
REPORT = API + "/gap-report-service"
REPORTS = REPORT + "/reportFormDatas"
INTEG = API + "/gap-integration-service"


def page(http, url, params, page_no, size):
    o = http.json(url, params={**params, "page": page_no, "size": size})
    if isinstance(o, list):
        return o, None
    return o.get("content") or [], o


def slices(http, url, params, offset, count, sub):
    """Rows offset..offset+count in pages of `sub`; a slice the backend cannot build (5xx) is split further."""
    rows = []
    for start in range(offset, offset + count, sub):
        try:
            rows += page(http, url, params, start // sub, sub)[0]
        except RuntimeError:
            if sub == 1:
                raise
            rows += slices(http, url, params, start, sub, max(1, sub // 5))
    return rows


def pages_all(http, url, params, size=200, on_page=None, cache_dir=None):
    """Every row of a Spring page endpoint. Returns (rows, server_total); big pages that fail are re-fetched in
    smaller slices so a single bad page never truncates the result."""
    rows, total, n = [], None, 0
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
    while True:
        cached = cache_dir / f"page_{n:05}.json" if cache_dir is not None else None
        if cached is not None and cached.exists():
            saved = json.loads(cached.read_text(encoding="utf-8"))
            got, meta = saved["rows"], saved["meta"]
        else:
            try:
                got, meta = page(http, url, params, n, size)
            except RuntimeError:
                got, meta = slices(http, url, params, n * size, size, max(1, size // 5)), None
        if meta is None and n == 0 and total is None and not got:
            return rows, 0
        if meta is not None:
            total = meta.get("totalElements", total)
            pages = meta.get("totalPages", 0)
        else:
            pages = -(-(total or 0) // size) if total else n + 2
        if cached is not None and not cached.exists():
            saved_meta = meta if meta is not None else ({"totalElements": total, "totalPages": pages} if total is not None else None)
            tmp = cached.with_suffix(".tmp")
            tmp.write_text(json.dumps({"rows": got, "meta": saved_meta}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(cached)
        rows += got
        if on_page:
            on_page(len(rows), total)
        n += 1
        if (meta is None and total is None and len(got) < size) or n >= pages:
            break
    if total is not None and len(rows) < total:
        log(f"short: {len(rows)} of {total} at {url.rsplit('/', 1)[-1]}")
    return rows, total
