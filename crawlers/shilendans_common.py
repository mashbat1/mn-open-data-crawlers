"""Shared pieces for the Shilendans (shilendans.gov.mn) gap-filling crawlers: endpoints and Spring paging."""
from .core import log

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


def pages_all(http, url, params, size=200):
    """Every row of a Spring page endpoint. Returns (rows, server_total); big pages that fail are re-fetched in
    smaller slices so a single bad page never truncates the result."""
    rows, total, n = [], None, 0
    while True:
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
        rows += got
        n += 1
        if (meta is None and total is None and len(got) < size) or n >= pages:
            break
    if total is not None and len(rows) < total:
        log(f"short: {len(rows)} of {total} at {url.rsplit('/', 1)[-1]}")
    return rows, total
