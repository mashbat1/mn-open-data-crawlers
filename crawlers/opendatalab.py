"""opendatalab.mn (Open Data Lab Mongolia, run by the NGO "Бодлогод залуусын хяналт") — a public archive of official
data. Collected here:
  procurements      every procurement in the list (number, title, customer, budget, method, funding, date)
  bidders           every bidder page (name, activity, address) and the tenders it took part in with its outcome
                    (Шалгарсан = selected, Хасагдсан = rejected, Илгээгдсэн = submitted)
  pages             election, mining-licence, local-development-fund, licence and open-budget pages, as text plus
                    label/value fields and links, for parsing downstream
Company pages (/search/<register>) repeat the legal-entity registry and asset declarations repeat xacxom.iaac.mn, so
neither is fetched. robots.txt asks for a 10 s crawl delay; the full crawl is ~30k requests (~3.5 days on Actions).
Terms: the database is copyrighted; cite opendatalab.mn and verify facts against the primary official source."""
import re
import urllib.parse as up

from .core import Http, NotFound, html_text, log, strip_tags

BASE = "https://www.opendatalab.mn"
PER_PAGE = 200
KEYS = {"procurements": ["id"], "bidders": ["id"], "bidder_tenders": ["bidder_id", "procurement_id", "seq"],
        "pages": ["url"], "unavailable": ["url"]}
http = Http(rate=0.1, timeout=180, retries=6)   # Crawl-delay: 10

REGIONS = ["Архангай", "Баян-Өлгий", "Баянхонгор", "Булган", "Говь-Алтай", "Говьсүмбэр", "Дархан-Уул", "Дорноговь",
           "Дорнод", "Дундговь", "Завхан", "Орхон", "Өвөрхангай", "Өмнөговь", "Сүхбаатар", "Сэлэнгэ", "Төв", "Увс",
           "Улаанбаатар", "Ховд", "Хөвсгөл", "Хэнтий"]
# Section pages and the links followed from them (each pattern is matched against the path + query)
FOLLOW = {
    "election": re.compile(r"^/election/[^?#]+$"),
    "mininglicense": re.compile(r"^/mininglicense/(?:region/[^?#/]+|\d+)$"),
    "localfund": re.compile(r"^/localfund/\d+(?:/projects(?:\?page=\d+)?|/project/\d+)?$"),
    "licenses": re.compile(r"^/licenses\?oid=\d+(?:&page=\d+)?$"),
}
MAX_DEPTH = 5


def get(path):
    return http.get(BASE + path).text


def main_html(html):
    """The page body without navigation, the cookie banner and the terms dialog that every page repeats."""
    html = re.sub(r"(?s)<!--.*?-->", "", html)
    m = re.search(r"(?s)<main[^>]*>(.*?)</main>", html)
    return m.group(1) if m else html


def fields(html):
    """Label/value pairs laid out as <div class="col-4">label</div><div class="col-8">value</div>."""
    out = {}
    for label, value in re.findall(r'(?s)<div class="col-4[^"]*">(.*?)</div>\s*<div class="col-8[^"]*">(.*?)</div>', html):
        k = strip_tags(label).strip()
        if k and k not in out:
            out[k] = strip_tags(value).strip()
    return out


def money(s):
    m = re.search(r"[\d,]+(?:\.\d+)?", s or "")
    return float(m.group(0).replace(",", "")) if m else None


# ---- procurements ----
def list_page(page):
    html = get(f"/procurement?per_page={PER_PAGE}&page={page}")
    rows = []
    for pid, card in re.findall(r'(?s)href="/search/(\d+)\?model=TProcurement">(.*?)</a>', html):
        p = {strip_tags(k).strip(" :"): strip_tags(v).strip() for k, v in re.findall(r"(?s)<strong>([^<]*)</strong>(.*?)</p>", card)}
        number = re.search(r'(?s)<span class="text-secondary[^"]*">(.*?)</span>', card)
        title = re.search(r'(?s)<strong class="h6[^"]*">(.*?)</strong>', card)
        funding = re.search(r"Тухайн онд санхүүжих:\s*([\d,\.]+)", card)
        badges = [strip_tags(b).strip() for b in re.findall(r'(?s)<span class="badge[^"]*">(.*?)</span>', card)]
        budget = p.get("Төсөвт өртөг", "").split("Тухайн")[0]
        rows.append({"id": int(pid), "number": strip_tags(number.group(1)).strip() if number else None,
                     "title": strip_tags(title.group(1)).strip() if title else None,
                     "opened": p.get("Нээгдсэн"), "customer": p.get("Захиалагч"), "budget": money(budget),
                     "funding_this_year": money(funding.group(1)) if funding else None,
                     "type": badges[0] if badges else None, "method": badges[1] if len(badges) > 1 else None,
                     "source_of_funds": badges[2] if len(badges) > 2 else None, "badges": "; ".join(badges),
                     "list_page": page})
    last = max([int(x) for x in re.findall(r"[?&]page=(\d+)", html)] + [page])
    return rows, last


def crawl_procurements(ctx):
    st = ctx.state
    if "proc_pages" not in st:
        rows, last = list_page(1)
        st["proc_pages"] = last
        ctx.save()
        log(f"procurements: {last} list pages of {PER_PAGE}")
    seen = st.setdefault("proc_ids", {})

    def one(page):
        rows, _ = list_page(page)
        if not rows or (len(rows) < PER_PAGE and page != st["proc_pages"]):
            raise RuntimeError(f"page {page}: {len(rows)} rows")
        for r in rows:
            ctx.write("procurements", r)
            seen[str(r["id"])] = 1

    ok = ctx.run_items("procurement_pages", range(1, st["proc_pages"] + 1), one, workers=1)
    # The list is ordered by date and keeps growing; items that moved between pages during the crawl are missed.
    # Bidder pages link procurements too; any linked id not seen in the lists is reported, not silently ignored.
    return ok


# ---- bidders ----
def bidder(bid):
    try:
        html = main_html(get(f"/search/{bid}?model=TBidder"))
    except NotFound:
        return None
    f = fields(html)
    if not f:
        raise RuntimeError("no fields")
    tenders = []
    part = html.split("Оролцсон тендер", 1)[1] if "Оролцсон тендер" in html else ""
    for seq, (pid, name, status) in enumerate(re.findall(
            r'(?s)href="/search/(\d+)\?model=TProcurement"[^>]*>(.*?)</a>.*?<div[^>]*>\s*([^<]*?)\s*</div>', part), 1):
        tenders.append({"bidder_id": bid, "procurement_id": int(pid), "seq": seq, "title": strip_tags(name).strip(),
                        "status": strip_tags(status).strip()})
    return {"id": bid, "name": f.get("Нэр"), "activity": f.get("Үйл ажиллагаа"), "registered": f.get("Бүртгүүлсэн"),
            "country": f.get("Улс"), "address": f.get("Хаяг"), "fields": f, "tenders": len(tenders)}, tenders


def find_last(model, lo, hi):
    """Highest id that has a page (ids are sequential; a few gaps are tolerated by probing several neighbours)."""
    def exists(n):
        for k in range(n, n + 5):
            try:
                get(f"/search/{k}?model={model}")
                return True
            except NotFound:
                continue
        return False
    while exists(hi) and hi < 10**6:
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if exists(mid) else (lo, mid)
    return lo + 4


def crawl_bidders(ctx):
    st = ctx.state
    if "bidder_last" not in st:
        st["bidder_last"] = find_last("TBidder", 20000, 40000) if not ctx.sample else 2160
        ctx.save()
        log(f"bidders: ids up to {st['bidder_last']}")
    missing = st.setdefault("bidder_missing", {})

    def one(bid):
        r = bidder(bid)
        if r is None:
            missing[str(bid)] = 1
            return
        row, tenders = r
        ctx.write("bidders", row)
        for t in tenders:
            ctx.write("bidder_tenders", t)

    ids = range(1, st["bidder_last"] + 1) if not ctx.sample else range(2153, 2158)
    ok = ctx.run_items("bidders", ids, one, workers=1)
    ctx.expect("bidders", len(ids) - len(missing))
    return ok


# ---- other sections: breadth-first over section pages ----
def seeds():
    out = [("/election/parliament", "election"), ("/election/president", "election"), ("/election/local", "election"),
           ("/licenses", "licenses")]
    out += [("/mininglicense/region/" + up.quote(r), "mininglicense") for r in REGIONS]
    out += [(f"/localfund/{i}", "localfund") for i in range(1, 23)]
    return out


def crawl_sections(ctx):
    st = ctx.state
    frontier = st.setdefault("frontier", {p: [s, 0] for p, s in seeds()})
    ok = True
    for depth in range(MAX_DEPTH + 1):
        level = [p for p, (s, d) in frontier.items() if d == depth]
        if not level:
            continue

        def one(path):
            section, d = frontier[path]
            try:
                html = get(path)
            except NotFound:
                return
            except RuntimeError as e:
                if "HTTP 500" not in str(e):
                    raise
                # the server fails on this page every time (e.g. two local-election years): list it, don't loop
                ctx.write("unavailable", {"url": BASE + path, "section": section, "error": str(e)[:200]})
                return
            body = main_html(html)
            title = re.search(r"(?s)<title>(.*?)</title>", html)
            links = sorted(set(up.unquote(h.replace("&amp;", "&")) for h in re.findall(r'href="(/[^"#]+)"', body)))
            ctx.write("pages", {"url": BASE + path, "section": section, "depth": d,
                                "title": strip_tags(title.group(1)).strip() if title else None,
                                "fields": fields(body), "text": html_text(body), "links": links})
            if d < MAX_DEPTH:
                for link in links:
                    path2 = up.quote(link, safe="/?=&")
                    for sec, pat in FOLLOW.items():
                        if pat.match(link) and path2 not in frontier:
                            frontier[path2] = [sec, d + 1]

        ok &= ctx.run_items(f"sections_{depth}", ctx.limit(level), one, workers=1)
        if ctx.sample:
            break
    return ok


def crawl_openbudget(ctx):
    st = ctx.state
    if "ob_pages" not in st:
        html = get(f"/openbudget?per_page={PER_PAGE}&page=1")
        st["ob_pages"] = max([int(x) for x in re.findall(r"[?&]page=(\d+)", html)] + [1])
        ctx.save()

    def one(page):
        path = f"/openbudget?per_page={PER_PAGE}&page={page}"
        body = main_html(get(path))
        items = re.findall(r'href="/search/(\d+)\?model=GTransaction"', body)
        if not items:
            raise RuntimeError("empty page")
        ctx.write("pages", {"url": BASE + path, "section": "openbudget", "depth": 0, "title": None, "fields": {},
                            "text": html_text(body), "links": [f"/search/{i}?model=GTransaction" for i in items]})

    return ctx.run_items("openbudget_pages", ctx.limit(range(1, st["ob_pages"] + 1)), one, workers=1)


def crawl(ctx):
    ok = crawl_procurements(ctx)
    ok &= crawl_sections(ctx)
    ok &= crawl_openbudget(ctx)
    ok &= crawl_bidders(ctx)
    return ok
