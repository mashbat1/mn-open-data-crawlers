"""supremecourt.mn (Supreme Court of Mongolia) — published supervisory-instance resolutions (тогтоол) of the
criminal, civil and administrative chambers and disciplinary cases, with full text; judges' dissenting opinions
(тусгай санал); the hearing calendar (every scheduled hearing: chamber, panel, rapporteur, outcome); and the list
of official interpretations, recommendations and reviews (тайлбар, зөвлөмж, тойм — PDF links only).

The site has no robots.txt. Its JSON endpoints are the ones the public pages call. Decision texts arrive inline in
the list endpoint, so no per-decision page is fetched. Parties are anonymised by the court; the endpoint's
un-anonymised party-name search field (`fiters`) is deliberately not stored."""
import datetime as dt
import re

from .core import Http, flat, html_text, log, strip_tags

BASE = "https://supremecourt.mn/mn"
KEYS = {"decisions": ["id"], "hearings": ["id"], "dissents": ["print_id", "tp", "s"], "advice": ["id"]}
FIRST_MONTH = (2019, 1)      # the calendar and the decision list are dense from 2019; earlier dates fetched at once
EARLY = ("1990-01-01", "2018-12-31")
ADVICE_TYPES = {1: "Тайлбар", 2: "Зөвлөмж", 3: "Тойм"}
ADVICE_CHAMBERS = {1: "Эрүүгийн", 2: "Иргэний", 3: "Захиргааны", 5: "Нийт шүүгчид", 7: "Бусад"}
DECISION_DROP = {"decisions", "fiters", "agent", "advicedate", "advicenumber", "advicemaintitle", "advicecontent",
                 "adviceactiveis"}
HEARING_HTML = ("actions", "parties", "summ_decs")
http = Http(rate=1.0, timeout=180, retries=6, headers={"X-Requested-With": "XMLHttpRequest"})


def months(last_offset=0):
    """'YYYY-MM' keys from FIRST_MONTH to the current month (+ last_offset months for scheduled hearings)."""
    today = dt.date.today()
    y, m = FIRST_MONTH
    end = today.year * 12 + today.month - 1 + last_offset
    out = []
    while y * 12 + m - 1 <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_range(key):
    if key == "early":
        return EARLY
    y, m = map(int, key.split("-"))
    nxt = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
    return f"{y:04d}-{m:02d}-01", (nxt - dt.timedelta(days=1)).isoformat()


def text_of(html):
    return html_text(html or "") or None


def crawl(ctx):
    complete = True
    st = ctx.state

    # ---- resolutions: one list request per month returns every decision with its full text ----
    def decisions(key):
        a, b = month_range(key)
        o = http.get(f"{BASE}/getDecisions/", params={"datea": a, "dateb": b}).json()
        decs = o.get("decs")
        if not isinstance(decs, list):
            raise RuntimeError("unexpected getDecisions response")
        reported = sum(int(c.get("cnt") or 0) for c in o.get("chamberCounts") or [])
        ctx.expect(f"decisions:{key}", reported or len(decs))
        for d in decs:
            text = text_of(d.get("decisions"))
            row = flat({k: v for k, v in d.items() if k not in DECISION_DROP})
            row.update(url=f"https://supremecourt.mn/mn/printc?id={d.get('zar')}", text=text, chars=len(text or ""))
            ctx.write("decisions", row)

    keys = ["early"] + months()
    if ctx.sample:
        keys = keys[-2:]
    complete &= ctx.run_items("decisions", keys, decisions, workers=1)

    # ---- hearing calendar: which days had hearings, then every hearing of each day ----
    days = st.setdefault("hearing_days", {})

    def month(key):
        a, b = month_range(key)
        for x in http.get(f"{BASE}/getAnnsOfMonth/{a}/{b}").json():
            if x.get("date"):
                days[x["date"]] = 1

    keys = ["early"] + months(last_offset=2)
    if ctx.sample:
        keys = keys[-4:-2]
    complete &= ctx.run_items("calendar", keys, month, workers=1)

    def day(date):
        rows = http.get(f"{BASE}/getAnnOfDay/{date}").json()
        if not isinstance(rows, list):
            raise RuntimeError("unexpected getAnnOfDay response")
        for h in rows:
            row = flat({k: v for k, v in h.items() if k not in HEARING_HTML})
            for k in HEARING_HTML:
                row[k] = text_of(h.get(k))
            row["date"] = date
            ctx.write("hearings", row)

    log(f"{len(days)} hearing days")
    complete &= ctx.run_items("hearing_days", sorted(days), day, workers=1)

    # ---- dissenting opinions: one server-rendered list, one print page per opinion ----
    if not ctx.stage_done("dissent_list"):
        html = http.get(f"{BASE}/home", params={"page": "courtorder", "id": 167, "pr": 166, "tp": "list", "h": 0}).text
        table = html[html.find('id="courtorderTable"'):]
        listed = {}
        for tr in re.findall(r"<tr\s+data-tankhim[^>]*>.*?</tr>", table, re.S):
            m = re.search(r'href="/?printc\?id=(\d+)"[^>]*>(.*?)</a>', tr, re.S)
            if not m:
                continue
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            chamber = re.search(r'data-tankhim="(\d*)"', tr)
            title = re.findall(r'href="/?printc\?id=\d+"[^>]*>(.*?)</a>', tr, re.S)
            for a in re.finditer(r'href="/?printa\?id=(\d+)&(?:amp;)?tp=(\d+)&(?:amp;)?s=(\d+)"[^>]*>(.*?)</a>', tr, re.S):
                judges = [strip_tags(j) for j in re.findall(r'fw-semibold">(.*?)</span>', a.group(4), re.S)]
                key = f"{a.group(1)}:{a.group(2)}:{a.group(3)}"
                listed[key] = {"print_id": int(m.group(1)), "tp": int(a.group(2)), "s": int(a.group(3)),
                               "number": strip_tags(m.group(2)), "date": strip_tags(tds[1]) if len(tds) > 1 else None,
                               "chamber_id": int(chamber.group(1)) if chamber and chamber.group(1) else None,
                               "chamber": strip_tags(re.search(r'badge[^"]*">(.*?)</span>', tr, re.S).group(1))
                               if re.search(r'badge[^"]*">(.*?)</span>', tr, re.S) else None,
                               "case": strip_tags(title[1]) if len(title) > 1 else None,
                               "rapporteur": strip_tags(tds[-1]) if tds else None,
                               "judges": "; ".join(judges)}
        counts = [int(n) for n in re.findall(r'fw-semibold fs-5">\s*(\d+)\s*<', html)]
        st["dissents"] = listed
        ctx.expect("dissents:decisions_with_dissent", sum(counts))
        ctx.expect("dissents:listed", len(listed))
        log(f"{len(listed)} dissenting opinions on {sum(counts)} decisions")
        ctx.mark_stage("dissent_list")

    def dissent(key):
        meta = st["dissents"][key]
        url = f"{BASE}/printa?id={meta['print_id']}&tp={meta['tp']}&s={meta['s']}"
        text = html_text(http.get(url).text, root_class="mainbx")
        if len(text) < 100:
            raise RuntimeError("no dissent text")
        ctx.write("dissents", dict(meta, url=url, chars=len(text), text=text))

    complete &= ctx.run_items("dissents", sorted(st["dissents"]), dissent, workers=1)

    # ---- interpretations, recommendations, reviews: metadata and PDF link from one JSON list ----
    if not ctx.stage_done("advice"):
        groups = http.get(f"{BASE}/getAdvice/").json()
        n = 0
        for g in groups:
            for a in g.get("advs") or []:
                pdf = a.get("pdf_file")
                ctx.write("advice", {"id": a.get("id"), "year": g.get("datec"), "date": a.get("date"),
                                     "number": a.get("tnumber"), "title": a.get("title"), "type": a.get("type"),
                                     "type_name": ADVICE_TYPES.get(a.get("type")), "chamber_id": a.get("tankhimid"),
                                     "chamber": ADVICE_CHAMBERS.get(a.get("tankhimid")), "links": a.get("links"),
                                     "pdf_url": f"https://supremecourt.mn{pdf}" if pdf and pdf.startswith("/") else pdf,
                                     "html_url": f"{BASE}/home?page=advice&id=15&print_id={a.get('id')}"
                                     if a.get("show_body") else None})
                n += 1
        ctx.expect("advice:all", n)
        log(f"{n} interpretations / recommendations / reviews")
        ctx.mark_stage("advice")
    return complete
