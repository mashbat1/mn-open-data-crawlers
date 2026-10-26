"""Civil Service Council (Төрийн албаны зөвлөл). csc.gov.mn is a link portal; the council's own site is
web.csc.gov.mn, whose pages are served from a public JSON API on port 8084. Collected: the site's menu tree and every
Mongolian-language content item (council decisions, sessions, appointment / transfer notices, exam-result list
announcements, dispute decisions, inspection reports, plans and reports, sector-council compositions, news) with full
text, the attached file's URL and every link in the text. Attachments (mostly PDFs, incl. the lists of citizens who
passed the civil-service exam) are recorded as URLs, never downloaded or parsed.
robots.txt: empty on csc.gov.mn, absent (404) on web.csc.gov.mn and on the API; requests are kept at 1 per second."""
import html as htmllib
import json
import re
from urllib.parse import urljoin

from .core import Http, html_text, log, strip_tags

SITE = "https://web.csc.gov.mn"
API = "https://web.csc.gov.mn:8084"
PAGE = 100
KEYS = {"menus": ["id"], "contents": ["id"], "links": ["content_id", "url"]}
http = Http(rate=1.0, timeout=120, retries=6)
LISTS = {"contents": "/saContent/list", "sector_councils": "/saContent/orgsList"}


def mn_lang():
    langs = http.json(f"{API}/saRefLang/list").get("rows") or []
    lang = next((r["_id"] for r in langs if r.get("code") == "MN"), None)
    if not lang:
        raise RuntimeError("CSC: Mongolian language id not found")
    return lang


def fetch_all(path, lang):
    """All rows of a list, paged in _id order (new items only append), checked against the server's total."""
    found, total, page = {}, None, 1
    params = {"limit": PAGE, "sorts[]": json.dumps({"value": 1, "name": "_id"})}
    if path.endswith("/list"):
        params["search"] = json.dumps([{"name": "langId", "value": lang}])
    while True:
        o = http.json(f"{API}{path}", params={**params, "page": page})
        if not isinstance(o, dict) or not isinstance(o.get("rows"), list) or not isinstance(o.get("total"), int):
            raise RuntimeError(f"CSC {path}: unexpected response shape")
        total = o["total"]
        for r in o["rows"]:
            found.setdefault(r["_id"], r)
        if not o["rows"] or page * PAGE >= total:
            break
        page += 1
    if len(found) < total:
        raise RuntimeError(f"CSC {path}: {len(found)} of {total} rows")
    return list(found.values()), total


def links_of(html):
    out = {}
    for m in re.finditer(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html or "", re.S | re.I):
        href = htmllib.unescape(m.group(1)).strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        out.setdefault(urljoin(SITE + "/", href), strip_tags(m.group(2)))
    for m in re.finditer(r"<(?:iframe|embed)\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", html or "", re.I):
        out.setdefault(urljoin(SITE + "/", htmllib.unescape(m.group(1)).strip()), None)
    return out


def crawl(ctx):
    st = ctx.state
    if not ctx.stage_done("menus"):
        st["lang"] = mn_lang()
        menus, menu_total = fetch_all("/saMenu/list", st["lang"])  # limits above a few hundred get HTTP 403
        names = {}
        for m in menus:
            names[m["_id"]] = (m.get("name") or "").strip()
            ctx.write("menus", {"id": m["_id"], "parent_id": m.get("parentMenuId"), "name": names[m["_id"]],
                                "link": m.get("link") or None, "level": m.get("level"), "active": m.get("isActive"),
                                "sort_order": m.get("sortOrder"), "menu_type_ids": json.dumps(m.get("menuTypeId") or []),
                                "url": f"{SITE}/contentList/{m['_id']}"})
        st["menu_names"] = names
        ctx.expect("menus", menu_total)
        log(f"{len(names)} menu entries")
        ctx.mark_stage("menus")
    names = st["menu_names"]

    def one_list(key):
        rows, total = fetch_all(LISTS[key], st["lang"])
        ctx.expect(f"contents:{key}", total)
        for r in rows:
            html = r.get("content") or ""
            body = html_text(html)
            menus = r.get("menuId") or []
            file_id = r.get("File") or None
            ctx.write("contents", {
                "id": r["_id"], "list": key, "title": (r.get("title") or "").strip(),
                "description": (r.get("description") or "").strip() or None,
                "publish_date": r.get("publishDate"), "created": r.get("createdAt"), "updated": r.get("updatedAt"),
                "menu_ids": json.dumps(menus), "menus": json.dumps([names.get(m) for m in menus], ensure_ascii=False),
                "category_ids": json.dumps(r.get("categoryId") or []), "org_id": r.get("createdOrg"),
                "file_url": f"{API}/core/fileMap/{file_id}" if file_id else None,
                "youtube": r.get("youtubeIframe") or None, "url": f"{SITE}/content/{r['_id']}",
                "text": body, "chars": len(body)})
            for url, label in links_of(html).items():
                ctx.write("links", {"content_id": r["_id"], "url": url, "text": label})
        log(f"{key}: {len(rows)} items")

    return ctx.run_items("lists", sorted(LISTS), one_list, workers=1)
