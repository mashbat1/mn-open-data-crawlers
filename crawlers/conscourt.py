"""conscourt.gov.mn (Constitutional Court of Mongolia, Үндсэн хуулийн цэц) and its successor site constitutionalcourt.mn,
to which the old site links as its "new version". Both are WordPress sites read through their public REST API:
every post (hearing notices and results, disputes opened, decision reviews, news), every page, and the PDF media
library. Decisions — conclusions (дүгнэлт), resolutions (тогтоол), rulings (магадлал) and the parliament resolutions
answering them — are taken from each site's decision index page and linked to the PDF/Word files their pages embed.
The decisions themselves are published only as PDF/DOCX; we record the file URLs, not the files. Decisions before
2023 exist only as multi-year PDF compilations (pages/documents tables).

conscourt.gov.mn has no robots.txt; constitutionalcourt.mn disallows only /wp-admin/ and one plugin file."""
import html as htmlmod
import json
import re
from urllib.parse import unquote, urlsplit

from .core import Http, html_text, log, strip_tags

SITES = {"old": ("https://conscourt.gov.mn", "https://conscourt.gov.mn/index.php?rest_route=/wp/v2/"),
         "new": ("https://constitutionalcourt.mn", "https://constitutionalcourt.mn/?rest_route=/wp/v2/")}
INDEX_PAGES = {"old": 10658, "new": 2051}           # "Үндсэн хуулийн цэцийн шийдвэр" on each site
TYPES = {"posts": "posts", "pages": "pages", "documents": "media"}
FIELDS = {"posts": "id,date,modified,link,slug,title,content,categories",
          "pages": "id,date,modified,link,slug,title,content,parent",
          "documents": "id,date,modified,title,source_url,mime_type,post,media_details"}
PER_PAGE = 20      # conscourt.gov.mn sometimes cuts long chunked responses short; small pages limit the damage
KEYS = {"posts": ["site", "id"], "pages": ["site", "id"], "documents": ["site", "id"], "decisions": ["site", "url"]}
DOC_RE = re.compile(r"""href=["']([^"']+\.(?:pdf|docx?|xlsx?|pptx?))["']""", re.I)
http = Http(rate=1.0, timeout=120, retries=6)


def documents(content):
    """File URLs a WordPress body embeds: dFlip viewer sources and plain links to office/PDF files."""
    out = []
    for s in re.findall(r'"source":"((?:[^"\\]|\\.)*)"', content or ""):
        try:
            out.append(json.loads(f'"{s}"'))
        except ValueError:
            out.append(s.replace("\\/", "/"))
    out += [htmlmod.unescape(u) for u in DOC_RE.findall(content or "")]
    return list(dict.fromkeys(u.strip() for u in out if u.strip()))


def norm(url, site):
    """A comparable form of a site URL: ('page'|'post', id) for ?page_id= / ?p= links, else the decoded path."""
    url = htmlmod.unescape(url or "").strip()
    m = re.search(r"[?&]page_id=(\d+)", url)
    if m:
        return f"id:{m.group(1)}"
    m = re.search(r"[?&]p=(\d+)", url)
    if m:
        return f"id:{m.group(1)}"
    if not re.match(r"^[a-z]+://", url):
        url = "https://" + url.lstrip("/")
    return "path:" + unquote(urlsplit(url).path).strip("/").lower()


def rest(site, kind, page, extra=None):
    host, api = SITES[site]
    params = {"per_page": PER_PAGE, "page": page, "orderby": "id", "order": "asc", "_fields": FIELDS[kind]}
    if kind == "documents":
        params["mime_type"] = "application/pdf"
    params.update(extra or {})
    r = get_json(api + TYPES[kind], params=params)
    return r.json(), int(r.headers.get("X-WP-Total") or 0), int(r.headers.get("X-WP-TotalPages") or 0)


def get_json(url, tries=3, **kw):
    """GET that insists on a complete JSON body (a truncated chunked response fails to parse; ask again)."""
    for attempt in range(tries):
        r = http.get(url, **kw)
        try:
            r.json()
            return r
        except ValueError:
            if attempt == tries - 1:
                raise RuntimeError(f"truncated JSON ({len(r.content)} bytes): {url}")
            log(f"truncated JSON from {url}, retrying")


def title_of(x):
    return htmlmod.unescape(strip_tags((x.get("title") or {}).get("rendered") or "")) or None


def crawl(ctx):
    complete = True
    st = ctx.state
    st.setdefault("cats", {})
    st.setdefault("totals", {})
    st.setdefault("page_map", {"old": {}, "new": {}})

    def categories(site):
        cats = {}
        p = 1
        while True:
            host, api = SITES[site]
            r = get_json(api + "categories", params={"per_page": 100, "page": p, "_fields": "id,name"})
            for c in r.json():
                cats[str(c["id"])] = htmlmod.unescape(c["name"])
            if p >= int(r.headers.get("X-WP-TotalPages") or 1):
                break
            p += 1
        st["cats"][site] = cats

    complete &= ctx.run_items("categories", list(SITES), categories, workers=1)

    def emit(site, kind, items):
        host = SITES[site][0]
        for x in items:
            if kind == "documents":
                md = x.get("media_details") or {}
                ctx.write("documents", {"site": site, "id": x["id"], "date": x.get("date"), "modified": x.get("modified"),
                                        "title": title_of(x), "url": x.get("source_url"), "mime_type": x.get("mime_type"),
                                        "attached_to": x.get("post"), "filesize": md.get("filesize")})
                continue
            content = (x.get("content") or {}).get("rendered") or ""
            text = html_text(content)
            link = x.get("link") or ""
            if site == "old" or not link.startswith("http"):   # the old site reports broken permalinks
                link = f"{host}/?{'page_id' if kind == 'pages' else 'p'}={x['id']}"
            docs = documents(content)
            row = {"site": site, "id": x["id"], "date": x.get("date"), "modified": x.get("modified"), "url": link,
                   "slug": unquote(x.get("slug") or ""), "title": title_of(x)}
            if kind == "posts":
                row["categories"] = "; ".join(st["cats"].get(site, {}).get(str(c), str(c)) for c in x.get("categories") or [])
            else:
                row["parent"] = x.get("parent")
                st["page_map"][site][str(x["id"])] = {"url": link, "rest_link": x.get("link"), "title": row["title"],
                                                     "docs": docs}
            row.update(documents=json.dumps(docs, ensure_ascii=False) if docs else None, chars=len(text), text=text)
            ctx.write(kind, row)

    # first page of every listing gives the server's totals
    def first(key):
        site, kind = key.split(":")
        items, total, pages = rest(site, kind, 1)
        st["totals"][key] = {"total": total, "pages": pages}
        # The media endpoint counts attachments it then hides (files of unpublished posts), so its total is an
        # upper bound and its pages can come back short or empty.
        ctx.expect(f"{kind}:{site}" + (":incl_hidden" if kind == "documents" else ""), total)
        emit(site, kind, items)

    firsts = [f"{s}:{k}" for k in ("pages", "posts", "documents") for s in SITES]
    complete &= ctx.run_items("first", firsts, first, workers=1)
    rest_pages = [f"{k}:{p}" for k, t in st["totals"].items() for p in range(2, t["pages"] + 1)]
    log(f"totals: {json.dumps({k: t['total'] for k, t in st['totals'].items()})}; {len(rest_pages)} more list pages")

    def more(key):
        site, kind, p = key.split(":")
        items, _, _ = rest(site, kind, int(p))
        if not items and kind != "documents":
            raise RuntimeError("empty page")
        emit(site, kind, items)

    complete &= ctx.run_items("pages", rest_pages, more, workers=1)

    # ---- decisions: the entries of each site's decision index page ----
    if not ctx.stage_done("decision_index"):
        index = {}
        for site, page_id in INDEX_PAGES.items():
            _, api = SITES[site]
            content = get_json(api + f"pages/{page_id}", params={"_fields": "id,content"}).json()["content"]["rendered"]
            entries, current = [], None
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.S) or [content]:
                cell = re.search(r'class="column-1"[^>]*>(.*?)</td>', tr, re.S)    # old site: the dispute's name
                subject = htmlmod.unescape(strip_tags(cell.group(1))) if cell else None
                for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', tr, re.S):
                    url, label = htmlmod.unescape(m.group(1)).strip(), htmlmod.unescape(strip_tags(m.group(2)))
                    low = label.lower()
                    is_decision = (re.search(r"дүгнэлт|тогтоол|магадлал", low) and "№" in label
                                   and not re.search(r"тойм|хуралдаан|хэлэлц|хүлээ", low))
                    if is_decision:
                        number = re.search(r"№\s*0*(\d+)", label)
                        date = re.search(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", label)
                        kind = ("УИХ-ын тогтоол" if "их хурлын" in low else
                                re.search(r"дүгнэлт|тогтоол|магадлал", low).group(0))
                        current = {"site": site, "url": url if url.startswith("http") else "https://" + url,
                                   "label": label, "kind": kind, "number": int(number.group(1)) if number else None,
                                   "date": "%s-%02d-%02d" % (date.group(1), int(date.group(2)), int(date.group(3)))
                                   if date else None, "subject": subject or None, "related": []}
                        entries.append(current)
                    elif current is not None:
                        current["related"].append({"title": label, "url": url})
            ctx.expect(f"decisions:{site}", len(entries))
            for e in entries:
                index[f"{site}|{e['url']}"] = e
            log(f"{site}: {len(entries)} decisions in the index page")
        st["decision_index"] = index
        ctx.mark_stage("decision_index")

    def decision(key):
        e = dict(st["decision_index"][key])
        site = e["site"]
        want = norm(e["url"], site)
        page_id, info = None, None
        for pid, p in st["page_map"].get(site, {}).items():
            if want in (f"id:{pid}", norm(p.get("rest_link") or p["url"], site)):
                page_id, info = pid, p
                break
        if info is None:   # not a page we listed (e.g. a post, or sample mode): read the page itself
            html = http.get(e["url"]).text
            if "</html>" not in html.lower():
                raise RuntimeError("truncated page")
            m = re.search(r"\b(?:page-id|postid)-(\d+)", html)
            page_id = m.group(1) if m else None
            t = re.search(r"<title>(.*?)</title>", html, re.S)
            info = {"title": htmlmod.unescape(strip_tags(t.group(1))) if t else None, "docs": documents(html)}
        docs = info["docs"]
        e.update(page_id=int(page_id) if page_id else None, page_title=info.get("title"),
                 pdf_url=next((d for d in docs if d.lower().endswith(".pdf")), None),
                 docx_url=next((d for d in docs if re.search(r"\.docx?$", d, re.I)), None),
                 documents=json.dumps(docs, ensure_ascii=False) if docs else None,
                 related=json.dumps(e["related"], ensure_ascii=False))
        ctx.write("decisions", e)

    complete &= ctx.run_items("decisions", sorted(st["decision_index"]), decision, workers=1)
    return complete
