"""khural.ulaanbaatar.mn (Capital City Citizens' Representative Khural, НИТХ) — from the site's public JSON API:
every published act (Khural resolutions "тогтоол", Presidium resolutions, binding rules, award rules ...) with full
text, every session (ээлжит / ээлжит бус / council / committee) with its published PDF links, the current
representatives, standing committees, transparency postings (HR, procurement, open-budget links) and the news archive.
Attached files (Word/PDF) are recorded as URL + name only. Representatives' e-mail and staff phone numbers are dropped.
robots.txt: none (404); requests are kept at 1 per second."""
import json

from .core import Http, html_text, log

BASE = "https://khural.ulaanbaatar.mn"
API = f"{BASE}/api"
PAGE = 50
MAX_PAGE = 3000   # whole lists are small (largest: ~2,100 news items)
KEYS = {"acts": ["id"], "sessions": ["id"], "members": ["id"], "committees": ["id"], "transparency": ["id"],
        "news": ["id"], "files": ["parent_table", "parent_id", "field", "url"]}
http = Http(rate=1.0, timeout=90, retries=6)

# list name -> (endpoint, table, extra query)
STATIC_LISTS = {
    "conference": ("conference", "sessions", {}),
    "leader": ("leader", "members", {}),
    "committee": ("committee", "committees", {}),
    "transparency": ("transparency", "transparency", {}),
    "article": ("article", "news", {}),
}


def text(html):
    return html_text(html) if html else None


def ref_names(type_code):
    return {r.get("code"): r.get("name") for r in http.json(f"{API}/reference-data/select", params={"typeCode": type_code})}


def list_page(endpoint, extra, page, size):
    o = http.json(f"{API}/{endpoint}", params={"currentPage": page, "pageSize": size, **extra})
    if not isinstance(o, dict) or not isinstance(o.get("list"), list) or "pagination" not in o:
        raise RuntimeError(f"{endpoint}: unexpected response shape")
    if o["pagination"].get("current") != page:  # the API ignores unknown paging parameters silently
        raise RuntimeError(f"{endpoint}: asked for page {page}, got {o['pagination'].get('current')}")
    return o["pagination"].get("total"), o["list"]


def fetch_all(endpoint, extra):
    """Every row of a list, checked against the server's total. Page order is not stable between requests (rows
    sharing a sort value swap places), so the whole list is asked for in one page, falling back to paging."""
    total, rows = list_page(endpoint, extra, 1, 1)
    if total is None:
        raise RuntimeError(f"{endpoint}: no total")
    found = {}
    if total <= MAX_PAGE:
        found = {r["id"]: r for r in list_page(endpoint, extra, 1, max(total, 1))[1]}
    page = 1
    while len(found) < total and (page - 1) * PAGE < total:
        for r in list_page(endpoint, extra, page, PAGE)[1]:
            found.setdefault(r["id"], r)
        page += 1
    if len(found) < total:
        raise RuntimeError(f"{endpoint} {extra}: {len(found)} of {total} rows")
    return list(found.values()), total


def files_of(value):
    if isinstance(value, dict):
        value = [value]
    # Some uploads were saved with a broken URL ("[object Object]"); their names are kept, the URL is not.
    return [{"name": f.get("name"), "url": f["url"] if str(f.get("url") or "").startswith("http") else None}
            for f in value or [] if isinstance(f, dict) and (f.get("url") or f.get("name"))]


def write_files(ctx, table, parent_id, field, value):
    for f in files_of(value):
        if not f["url"]:
            continue
        ctx.write("files", {"parent_table": table, "parent_id": parent_id, "field": field,
                            "name": f.get("name"), "url": f.get("url")})


def crawl(ctx):
    st = ctx.state
    if not ctx.stage_done("refs"):
        st["refs"] = {t: ref_names(t) for t in ("LAW", "CONFERENCE", "COMMITTEE")}
        ctx.mark_stage("refs")
    refs = st["refs"]
    lists = [f"law:{code}" for code in sorted(refs["LAW"])] + sorted(STATIC_LISTS)

    def one_list(key):
        if key.startswith("law:"):
            code = key.split(":", 1)[1]
            rows, total = fetch_all("law", {"code": code})
            ctx.expect(f"acts:{code}", total)
            for r in rows:
                if r.get("code") != code:
                    raise RuntimeError(f"law list {code} returned an act of type {r.get('code')}")
                body = text(r.get("content"))
                ctx.write("acts", {"id": r["id"], "category_code": code, "category": refs["LAW"].get(code),
                                   "number": r.get("number"), "title": r.get("title"), "date": r.get("date"),
                                   "created": r.get("createdDate"), "modified": r.get("modifiedDate"),
                                   "text": body, "chars": len(body or ""),
                                   "word_file_url": next((f["url"] for f in files_of(r.get("word"))), None),
                                   "word_file_name": next((f["name"] for f in files_of(r.get("word"))), None)})
                write_files(ctx, "acts", r["id"], "word", r.get("word"))
            log(f"acts {code}: {len(rows)}")
            return
        endpoint, table, extra = STATIC_LISTS[key]
        rows, total = fetch_all(endpoint, extra)
        ctx.expect(f"{table}:all", total)
        for r in rows:
            rid = r["id"]
            if table == "sessions":
                ctx.write(table, {"id": rid, "type_code": r.get("type"), "type": refs["CONFERENCE"].get(r.get("type")),
                                  "name": r.get("name"), "date": r.get("date"), "created": r.get("createdDate"),
                                  "content1": text(r.get("content1")), "content2": text(r.get("content2")),
                                  "pdf1": json.dumps(files_of(r.get("pdf1")), ensure_ascii=False),
                                  "pdf2": json.dumps(files_of(r.get("pdf2")), ensure_ascii=False)})
                write_files(ctx, table, rid, "pdf1", r.get("pdf1"))
                write_files(ctx, table, rid, "pdf2", r.get("pdf2"))
            elif table == "members":
                # e-mail deliberately not stored
                ctx.write(table, {"id": rid, "name": r.get("name"), "job": r.get("job"),
                                  "birth_year": r.get("birthYear"), "birth_place": r.get("birthPlace"),
                                  "education": text(r.get("education")), "work_experience": text(r.get("workExperience")),
                                  "awards": text(r.get("reward")), "image_url": (r.get("image") or {}).get("url"),
                                  "created": r.get("createdDate"), "modified": r.get("modifiedDate")})
            elif table == "committees":
                ctx.write(table, {"id": rid, "title": r.get("title"), "reference_id": r.get("referenceDataId"),
                                  "reference": r.get("referenceDataName"), "description": text(r.get("desc")),
                                  "content": text(r.get("content")), "role": text(r.get("role")),
                                  "members": text(r.get("member")), "created": r.get("createdDate")})
            elif table == "transparency":
                # phone / room number deliberately not stored
                ctx.write(table, {"id": rid, "code": r.get("code"), "tab": r.get("tabTitle"), "title": r.get("title"),
                                  "content": text(r.get("content")), "name": r.get("name"),
                                  "department": r.get("department"), "position": r.get("position"),
                                  "link": r.get("link"), "date1": r.get("date1"), "date2": r.get("date2"),
                                  "created": r.get("createdDate")})
            elif table == "news":
                st["news_ids"][rid] = 1   # the list omits the article body; fetched one by one below
        log(f"{table}: {len(rows)}")

    st.setdefault("news_ids", {})
    complete = ctx.run_items("lists", lists, one_list, workers=1)

    def news(rid):
        r = http.json(f"{API}/article/{rid}")
        if not isinstance(r, dict) or r.get("id") != rid:
            raise RuntimeError(f"article {rid}: unexpected response")
        body = text(r.get("content"))
        ctx.write("news", {"id": rid, "url_id": r.get("urlId"), "title": r.get("title"), "date": r.get("date"),
                           "type": r.get("multiType"), "news_type": r.get("newsType"), "code": r.get("code"),
                           "council": r.get("councilName"), "representative": r.get("representativeName"),
                           "source": r.get("source"), "url": r.get("url"), "views": r.get("viewCount"),
                           "text": body, "chars": len(body or ""), "created": r.get("createdDate"),
                           "modified": r.get("modifiedDate")})

    complete &= ctx.run_items("news", sorted(st["news_ids"]), news, workers=2)
    return complete
