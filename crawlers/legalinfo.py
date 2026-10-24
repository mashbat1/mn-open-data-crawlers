"""legalinfo.mn — every legal act (in force, repealed, suspended): metadata, full text, amendment links."""
import datetime as dt
import math
import re

from .core import Http, html_text, log, strip_tags

BASE = "https://legalinfo.mn/mn"
KEYS = {"acts": ["law_id", "category", "active"], "texts": ["law_id"], "links": ["law_id", "linked_law_id"]}
CATEGORIES = range(27, 47)      # filtercategorytypeid; code = id - 26 (27/1 laws, 33/7 gov. resolutions ...)
ACTIVE = (1, 0, 2)              # in force, repealed, suspended
PAGE = 20
http = Http(rate=3.0, timeout=120, headers={"X-Requested-With": "XMLHttpRequest"})


def list_page(cat, active, page, start=None, end=None, order="asc"):
    data = {"filtercategorytypeid": cat, "code": cat - 26, "isactive": active, "page": page,
            "sort": "enacteddate", "sortType": order}
    if start:
        data.update(filterstartdate=start, filterenddate=end)
    html = http.post(f"{BASE}/ajaxListBody/", data=data).json().get("Html") or ""
    total = re.search(r"Нийт\s*(\d+)", html)
    rows = []
    for chunk in html.split("legal-list-component")[1:]:
        m = re.search(r"lawId=(\d+)", chunk)
        if not m:
            continue
        # each block runs from the rest of its opening tag to the start of the next block's tag
        blocks = dict((k, strip_tags(re.sub(r"<[^>]*$", "", v.split(">", 1)[-1]))) for k, v in re.findall(r'data-block="(\w+)"(.*?)(?=data-block="|\Z)', chunk, re.S))
        title = re.search(r'class="act-name[^"]*">(.*?)</a>', chunk, re.S)
        rows.append({"law_id": m.group(1), "category": cat, "active": active,
                     "number": blocks.get("actnumber"), "title": strip_tags(title.group(1)) if title else blocks.get("title"),
                     "enacted": blocks.get("enacteddate"),
                     "blocks": {k: v for k, v in blocks.items() if k not in ("id", "actnumber", "title")}})
    return (int(total.group(1)) if total else None), rows


def crawl(ctx):
    complete = True
    st = ctx.state
    st.setdefault("law_ids", {})
    if not ctx.stage_done("totals"):
        totals = {}
        for cat in CATEGORIES:
            for active in ACTIVE:
                total, _ = list_page(cat, active, 1)
                if total:
                    totals[f"{cat}:{active}"] = total
                    ctx.expect(f"acts:{cat}:{active}", total)
                if ctx.sample and len(totals) >= 2:
                    break
            if ctx.sample and len(totals) >= 2:
                break
        st["totals"] = totals
        log(f"totals: {len(totals)} category/status lists, {sum(totals.values())} acts")
        ctx.mark_stage("totals")
    pages = [f"{k}:{p}" for k, n in st["totals"].items() for p in range(1, math.ceil(n / PAGE) + 1)]

    def one_page(key):
        cat, active, page = map(int, key.split(":"))
        _, rows = list_page(cat, active, page)
        if not rows:
            raise RuntimeError("empty page")
        for r in rows:
            ctx.write("acts", r)
            st["law_ids"][r["law_id"]] = cat
            st["listed"].setdefault(f"{cat}:{active}", {})[r["law_id"]] = 1

    st.setdefault("listed", {})
    complete &= ctx.run_items("list", pages, one_page, workers=3)

    # Pages are ordered by enactment date; acts sharing a date swap places between page requests, so some appear
    # twice and others never. Lists that come up short are re-read in date windows small enough for one page.
    def windows(key):
        cat, active = map(int, key.split(":"))
        seen = st["listed"].setdefault(key, {})

        def take(rows):
            for r in rows:
                if r["law_id"] not in seen:
                    seen[r["law_id"]] = 1
                    st["law_ids"][r["law_id"]] = cat
                    ctx.write("acts", r)

        def walk(a, b):
            total, rows = list_page(cat, active, 1, a.isoformat(), b.isoformat())
            if not total:
                return
            if total <= PAGE or a == b:
                take(rows)
                for p in range(2, math.ceil(total / PAGE) + 1):  # a single day with more acts than a page
                    for order in ("asc", "desc"):
                        take(list_page(cat, active, p, a.isoformat(), b.isoformat(), order)[1])
                return
            mid = a + (b - a) // 2
            walk(a, mid)
            walk(mid + dt.timedelta(days=1), b)

        walk(dt.date(1900, 1, 1), dt.date.today())
        log(f"list {key}: {len(seen)} of {st['totals'][key]} after date windows")

    gaps = [k for k, n in st["totals"].items() if len(st["listed"].get(k, {})) < n]
    complete &= ctx.run_items("windows", gaps, windows, workers=2)

    def detail(law_id):
        html = http.get(f"{BASE}/detail", params={"lawId": law_id}).text
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        crumb = re.search(r'breadcrumb">(.*?)</(?:ul|ol|div)>', html, re.S)
        unid = re.search(r'data-unid="([^"]+)"', html)
        text = html_text(html, root_class="law_content", drop_classes=("nom-more-header", "media-link"))
        if not text:
            raise RuntimeError("no text")
        ctx.write("texts", {"law_id": law_id, "title": strip_tags(title.group(1)) if title else None,
                            "breadcrumb": strip_tags(crumb.group(1)) if crumb else None, "chars": len(text), "text": text})
        if unid:
            o = http.post(f"{BASE}/lawConnectedTypeData", data={"dvid": unid.group(1), "type": 2, "lawId": law_id}).json()
            h = o.get("Html") if isinstance(o, dict) else str(o)
            for block in re.split(r'(?=data-id=")', h or "")[1:]:
                m = re.search(r"getViewLaw\(this,\s*'(\d+)'\)[^>]*>(.*?)</a>", block, re.S)
                if not m or m.group(1) == law_id:
                    continue
                d = re.search(r"(\d{4}-\d{2}-\d{2})", block)
                ctx.write("links", {"law_id": law_id, "linked_law_id": m.group(1), "relation": "amended_by",
                                    "title": strip_tags(m.group(2)), "date": d.group(1) if d else None})

    complete &= ctx.run_items("detail", sorted(st["law_ids"]), detail, workers=4)
    return complete
