"""mof.gov.mn (Ministry of Finance) — every section of the site (/article/... and /download/... pages, found by
following the site's own menus), every entry listed in them (budget laws and guidelines, budget execution reports,
budget-result indicators, debt and securities reports, investor documents, procurement notices, news ...) with its
text, publishing department and date, and every attached file as URL + name + type. Attached spreadsheets
(xlsx / xlsm / csv, under 20 MB) are downloaded and parsed into rows; legacy .xls and all other files (PDF, Word ...)
are recorded only. The site's sitemap.xml dates from 2019, so it is not used.
Not duplicated here: the barred-supplier and tender-complaint tables that companies.py reads from opendata.gov.mn —
spreadsheets under the procurement (/tender/) sections are recorded but not parsed.
robots.txt: none (404); requests are kept at 1 per second."""
import csv
import datetime as dt
import html as htmllib
import io
import json
import re
from urllib.parse import unquote, urljoin, urlsplit

import openpyxl

from .core import Http, NotFound, html_text, log, strip_tags

BASE = "https://mof.gov.mn"
KEYS = {"pages": ["url"], "entries": ["slug"], "documents": ["parent_url", "url"],
        "table_files": ["file_url"], "table_rows": ["file_url", "sheet", "row_no"]}
http = Http(rate=1.0, timeout=120, retries=6)
MAX_TABLE_BYTES = 20 * 1024 * 1024
PARSED = {"xlsx", "xlsm", "csv"}
FILE_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "xlsm", "csv", "ppt", "pptx", "zip", "rar", "7z", "txt", "rtf", "odt",
            "ods", "json", "xml"}
SECTION = re.compile(r"^https?://(?:www\.)?mof\.gov\.mn/((?:article|download)/[^?#\"'\s]*)$")
# ExpressionEngine resolves a section by its last URL segment, so /article/accounting/NBBASKH,
# /article/transparency/accounting/NBBASKH, /article/new/NBBASKH and /article/features/NBBASKH are one list (the last
# two re-sorted). Sections are therefore keyed by their last segment and fetched once.
SKIP_FIRST = {"entry", "top", "features", "featured", "new"}
MAX_SECTIONS = 1000
CONTACT = re.compile(r"утас|утсны|имэйл|и-мэйл|e-?mail|цахим шуудан|факс|гэрийн хаяг|регистрийн дугаар", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
NEWEST = f"{BASE}/article/new"   # "Сүүлд нэмэгдсэн": every entry of the site, newest first, 9 per page


def norm_section(href):
    """(key, canonical url) of a site-section link, or None when the link is not a section."""
    url = urljoin(BASE + "/", htmllib.unescape(href.strip()))
    m = SECTION.match(url.split("#")[0].split("?")[0])
    if not m:
        return None
    path = re.sub(r"/P\d+/?$", "", m.group(1)).rstrip("/")
    parts = path.split("/")
    if len(parts) < 2 or (parts[0] == "article" and parts[1] in SKIP_FIRST):
        return None
    key = "/".join(parts[-2:]) if parts[-1].isdigit() else parts[-1]   # news/new/10: a news sub-list
    return f"{parts[0]}:{key}", f"{BASE}/{path}"


def entry_slug(href):
    url = urljoin(BASE + "/", htmllib.unescape(href.strip())).split("#")[0].split("?")[0]
    m = re.match(r"^https?://(?:www\.)?mof\.gov\.mn/article/entry/([^/\s]+)/?$", url)
    return unquote(m.group(1)) if m else None


def main_region(html):
    """The page's own content column: from the section title / article header down to the social links."""
    start = min([i for i in (html.find('class="section-title"'), html.find('class="article-detail'),
                             html.find('class="col-md-9"')) if i >= 0] or [0])
    end = html.find('class="app-social"', start)
    return html[start:end if end > 0 else len(html)]


def file_ext(url):
    path = unquote(urlsplit(url).path)
    m = re.search(r"\.([A-Za-z0-9]{2,5})$", path)
    return m.group(1).lower() if m else None


def file_links(region):
    """(url, label) pairs for links that point at downloadable files."""
    out = {}
    for m in re.finditer(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", region, re.S | re.I):
        url = urljoin(BASE + "/", htmllib.unescape(m.group(1).strip()))
        if not url.startswith("http"):
            continue
        if "/files/uploads/" in url or "/userfiles/files/" in url or file_ext(url) in FILE_EXT:
            out.setdefault(url, strip_tags(m.group(2)) or None)
    return out


class Scrub:
    """Personal contact details are not kept: columns headed phone / e-mail / home address / ID number are masked
    from the header row down, and stray e-mail addresses anywhere are masked."""

    def __init__(self):
        self.masked = set()

    def __call__(self, cells):
        heads = {i for i, c in enumerate(cells) if isinstance(c, str) and len(c) < 60 and CONTACT.search(c)}
        out = [("(хассан)" if i in self.masked and c not in (None, "") else
                EMAIL.sub("(хассан)", c) if isinstance(c, str) else c) for i, c in enumerate(cells)]
        self.masked |= heads
        return out


def mn_date(s):
    m = re.search(r"(\d{4})\s*оны\s*(\d{1,2})\s*сарын\s*(\d{1,2})", s or "")
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", s or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def parse_section(html):
    region = main_region(html)
    title = re.search(r'class="section-title"[^>]*>(.*?)</h1>', region, re.S)
    entries = []
    for m in re.finditer(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']", region, re.I):
        slug = entry_slug(m.group(1))
        if slug and slug not in entries:
            entries.append(slug)
    return region, (strip_tags(title.group(1)) if title else None), entries


def paging(region, url):
    offsets = sorted({int(p) for p in re.findall(re.escape(url) + r"/P(\d+)", region)})
    return (offsets[0], offsets[-1]) if offsets else (None, None)


def crawl(ctx):
    st = ctx.state
    complete = True
    st.setdefault("sections", {})    # key -> canonical url
    st.setdefault("entries", {})     # slug -> section url it was first listed in ("" = only in the newest list)
    st.setdefault("tables", {})      # spreadsheet url -> {parent, name, ext}

    def discover(html):
        for m in re.finditer(r"href\s*=\s*[\"']([^\"']+)[\"']", html):
            found = norm_section(m.group(1))
            if found and len(st["sections"]) < MAX_SECTIONS:
                st["sections"].setdefault(*found)

    def note_table(url, parent, label):
        ext = file_ext(url)
        if ext in PARSED or ext == "xls":
            st["tables"].setdefault(url, {"parent": parent, "name": label, "ext": ext})

    if not st["sections"]:
        discover(http.get(f"{BASE}/").text)
        for u in (f"{BASE}/download/citizen-budget", f"{BASE}/download/user_guide"):
            st["sections"].setdefault(*norm_section(u))
        log(f"{len(st['sections'])} sections linked from the home page")
        ctx.save()

    def section(key):
        url = st["sections"][key]
        r = http.get(url)
        html = r.text
        region, title, entries = parse_section(html)
        discover(html)
        for slug in entries:
            st["entries"].setdefault(slug, url)
        files = file_links(region)
        for f, label in files.items():
            ctx.write("documents", {"parent_url": url, "parent_kind": "section", "slug": None, "url": f,
                                    "name": label, "ext": file_ext(f)})
            note_table(f, url, label)
        step, last = paging(region, r.url.rstrip("/"))
        ctx.write("pages", {"key": key, "url": url, "final_url": r.url, "title": title,
                            "entries_on_first_page": len(entries), "page_step": step, "last_offset": last,
                            "files": len(files)})

    # 1. sections (first page of each), breadth first: each round fetches what the previous round linked to
    for _ in range(30):
        todo = [k for k in sorted(st["sections"]) if k not in ctx.done_set("sections")]
        if not todo or ctx.time_up():
            break
        complete &= ctx.run_items("sections", todo, section, workers=2)
        if ctx.sample:
            break
    complete &= all(k in ctx.done_set("sections") for k in st["sections"])
    ctx.expect("pages:sections", len(st["sections"]))

    # 2. the site-wide newest-first list enumerates every entry; its last page gives the server's total
    if "newest" not in st:
        region, _, entries = parse_section(http.get(NEWEST).text)
        step, last = paging(region, NEWEST)
        if not entries or not step:
            raise RuntimeError("MoF newest-entries list has no entries or no paging")
        for slug in entries:
            st["entries"].setdefault(slug, "")
        st["newest"] = {"step": step, "last": last}
        ctx.save()
    step, last = st["newest"]["step"], st["newest"]["last"]

    def newest_page(off):
        _, _, entries = parse_section(http.get(f"{NEWEST}/P{off}").text)
        if not entries:
            raise RuntimeError(f"empty newest-list page P{off}")
        for slug in entries:
            st["entries"].setdefault(slug, "")
        if int(off) == last:
            st["newest"]["last_count"] = len(entries)

    complete &= ctx.run_items("newest", [str(o) for o in range(step, last + 1, step)], newest_page, workers=2)
    if st["newest"].get("last_count") is not None:
        ctx.expect("entries:newest_list", last + st["newest"]["last_count"])

    # 3. entries
    def entry(slug):
        url = f"{BASE}/article/entry/{slug}"
        try:
            html = http.get(url).text
        except NotFound:
            ctx.write("entries", {"slug": slug, "url": url, "section": st["entries"].get(slug) or None, "status": "not_found"})
            return
        title = (re.search(r'var article_entry_title\s*=\s*"((?:[^"\\]|\\.)*)"', html)
                 or re.search(r'property="og:title"\s+content="([^"]*)"', html))
        dept = re.search(r"Мэдээлэл нийтлэсэн:\s*<span>(.*?)</span>", html, re.S)
        date = re.search(r"Нийтлэсэн огноо:\s*<span>(.*?)</span>", html, re.S)
        side = re.search(r'class="side-menu[^"]*">\s*<h4>(.*?)</h4>', html, re.S)
        active = re.search(r"<li class=['\"]active['\"]>\s*<a href=['\"]([^'\"]+)['\"]>(.*?)</a>", html, re.S)
        start = html.find('class="article-content-container"')
        end = html.find('class="text-right"', start)
        body = html[start:end] if start >= 0 else ""
        items = re.findall(r'<div class="article-item-container">(.*?)</div>', body, re.S)
        attachments = file_links("".join(items))
        inline = {u: t for u, t in file_links(re.sub(r'<div class="article-item-container">.*?</div>', "", body,
                                                     flags=re.S)).items() if u not in attachments}
        text = html_text("<div " + re.sub(r'<div class="article-item-container">.*?</div>', "", body, flags=re.S)) \
            if body else ""
        t = None
        if title:
            t = title.group(1)
            if title.re.pattern.startswith("var"):
                try:
                    t = json.loads(f'"{t}"')
                except ValueError:
                    pass
            t = htmllib.unescape(t).strip()
        for kind, links in (("attachment", attachments), ("inline", inline)):
            for f, label in links.items():
                ctx.write("documents", {"parent_url": url, "parent_kind": kind, "slug": slug, "url": f,
                                        "name": label, "ext": file_ext(f)})
                note_table(f, url, label)
        ctx.write("entries", {"slug": slug, "url": url, "status": "ok", "title": t,
                              "department": strip_tags(dept.group(1)) if dept else None,
                              "date": mn_date(date.group(1)) if date else None,
                              "date_text": strip_tags(date.group(1)) if date else None,
                              "section": st["entries"].get(slug) or None,
                              "side_menu": strip_tags(side.group(1)) if side else None,
                              "side_menu_active": strip_tags(active.group(2)) if active else None,
                              "side_menu_url": active.group(1) if active else None,
                              "attachments": len(attachments), "text": text, "chars": len(text)})

    complete &= ctx.run_items("entries", sorted(st["entries"]), entry, workers=2)

    # 4. spreadsheets
    def table(url):
        meta = st["tables"][url]
        row = {"file_url": url, "parent_url": meta["parent"], "name": meta["name"], "ext": meta["ext"]}
        if meta["ext"] == "xls":
            ctx.write("table_files", {**row, "status": "xls_not_parsed"})
            return
        if "/tender/" in meta["parent"] or "/tender-" in meta["parent"]:
            ctx.write("table_files", {**row, "status": "procurement_not_parsed"})
            return
        try:
            r = http.get(url, stream=True)
        except NotFound:
            ctx.write("table_files", {**row, "status": "not_found"})
            return
        size = int(r.headers.get("Content-Length") or 0)
        if size > MAX_TABLE_BYTES:
            r.close()
            ctx.write("table_files", {**row, "status": "too_large", "bytes": size})
            return
        buf = io.BytesIO()
        for chunk in r.iter_content(1 << 16):
            buf.write(chunk)
            if buf.tell() > MAX_TABLE_BYTES:
                r.close()
                ctx.write("table_files", {**row, "status": "too_large", "bytes": buf.tell()})
                return
        data = buf.getvalue()
        n_rows, sheets = 0, []
        if meta["ext"] == "csv":
            for enc in ("utf-8-sig", "cp1251"):
                try:
                    content = data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                content = data.decode("utf-8", "replace")
            sheets.append("csv")
            scrub = Scrub()
            for i, cells in enumerate(csv.reader(io.StringIO(content)), 1):
                if any(c.strip() for c in cells):
                    cells = scrub(cells)
                    ctx.write("table_rows", {"file_url": url, "sheet": "csv", "row_no": i,
                                             "cells": json.dumps(cells, ensure_ascii=False)})
                    n_rows += 1
        else:
            try:
                wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            except Exception as e:  # noqa: BLE001 - a corrupt or mislabelled file is recorded, not retried
                ctx.write("table_files", {**row, "status": f"unreadable: {type(e).__name__}", "bytes": len(data)})
                return
            for ws in wb.worksheets:
                sheets.append(ws.title)
                scrub = Scrub()
                for i, values in enumerate(ws.iter_rows(values_only=True), 1):
                    cells = [None if v is None else (v.isoformat() if hasattr(v, "isoformat") else v) for v in values]
                    while cells and cells[-1] in (None, ""):
                        cells.pop()
                    if not cells:
                        continue
                    cells = scrub(cells)
                    ctx.write("table_rows", {"file_url": url, "sheet": ws.title, "row_no": i,
                                             "cells": json.dumps(cells, ensure_ascii=False, default=str)})
                    n_rows += 1
            wb.close()
        ctx.write("table_files", {**row, "status": "parsed", "bytes": len(data), "sheets": json.dumps(sheets, ensure_ascii=False),
                                  "rows": n_rows})

    complete &= ctx.run_items("tables", sorted(st["tables"]), table, workers=1)
    return complete
