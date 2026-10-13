"""shuukh.mn — published court decisions relevant to integrity: criminal corruption chapter 22 (2017 code) and
administrative disputes over tenders, state property, state inspection and civil service. Parties are
anonymised by the courts; we keep only metadata and decision text."""
import datetime as dt
import math
import re

from .core import Http, html_text, log, strip_tags

BASE = "https://shuukh.mn"
KEYS = {"cases": ["case_id", "inst", "court_cat"], "decisions": ["case_id", "inst", "court_cat"]}
CORRUPTION = list(range(871, 903)) + [1139]          # article ids of Criminal Code ch. 22 (22.1.1 ... 22.13)
ADMIN_TYPES = {5: "tender", 11: "state_property", 10: "state_inspection", 14: "civil_service"}
INSTANCES = (1, 2, 3)                                # first instance, appeal, supervisory
FIRST_YEAR = 2017
http = Http(rate=0.3, timeout=90, retries=8, headers={"X-Requested-With": "XMLHttpRequest"})


def queries(sample=False):
    years = range(FIRST_YEAR, dt.date.today().year + 1)
    out = []
    for y in years:
        for inst in INSTANCES:
            out.append(("crim", 2, inst, "ch22", y))
            for z in ADMIN_TYPES:
                out.append(("admin", 3, inst, str(z), y))
    return out[-4:] if sample else out


def params(cat, inst, zuil, year, page):
    p = [("id", inst), ("court_cat", cat), ("bb", 1), ("page", page),
         ("daterange", f"{year}/01/01 - {year}/12/31"), ("court_order", 1), ("date_order", 1)]
    for z in (CORRUPTION if zuil == "ch22" else [int(zuil)]):
        p.append(("zuil[]", z))
    return p


def list_page(cat, inst, zuil, year, page):
    o = http.get(f"{BASE}/site/case_ajax", params=params(cat, inst, zuil, year, page)).json()
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", o.get("view") or "", re.S):
        m = re.search(r"/single_case/(\d+)", tr)
        if not m:
            continue
        tds = [strip_tags(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        flags = re.findall(r'title="([^"]+)"', tr)
        rows.append({"case_id": int(m.group(1)), "inst": inst, "court_cat": cat, "query": zuil, "year": year,
                     "court": tds[0] if tds else None, "date": tds[1] if len(tds) > 1 else None,
                     "number": tds[2] if len(tds) > 2 else None, "judge": tds[5] if len(tds) > 5 else None,
                     "articles": tds[6] if len(tds) > 6 else None, "flags": "; ".join(flags)})
    return int(o.get("count") or 0), rows


def crawl(ctx):
    complete = True
    st = ctx.state
    st.setdefault("counts", {})
    st.setdefault("cases", {})

    def count(q):
        _, cat, inst, zuil, y = q
        n, rows = list_page(cat, inst, zuil, y, 1)
        st["counts"][":".join(map(str, q))] = n
        for r in rows:
            ctx.write("cases", r)
            st["cases"][f"{r['case_id']}:{inst}:{cat}"] = 1

    complete &= ctx.run_items("count", queries(ctx.sample), count, key=lambda q: ":".join(map(str, q)), workers=1)
    for k, n in st["counts"].items():
        ctx.expect(f"cases:{k}", n)
    pages = [f"{k}:{p}" for k, n in st["counts"].items() for p in range(2, math.ceil(n / 10) + 1)]
    log(f"{sum(st['counts'].values())} cases listed by the server, {len(pages)} more list pages")

    def page(key):
        kind, cat, inst, zuil, y, p = key.split(":")
        _, rows = list_page(int(cat), int(inst), zuil, int(y), int(p))
        if not rows:
            raise RuntimeError("empty page")
        for r in rows:
            ctx.write("cases", r)
            st["cases"][f"{r['case_id']}:{inst}:{cat}"] = 1

    complete &= ctx.run_items("pages", pages, page, workers=1)

    def decision(key):
        case_id, inst, cat = key.split(":")
        html = http.get(f"{BASE}/single_case/{case_id}", params={"id": inst, "court_cat": cat, "bb": 1}).text
        text = html_text(html)
        if len(text) < 200:
            raise RuntimeError("no decision text")
        ctx.write("decisions", {"case_id": int(case_id), "inst": int(inst), "court_cat": int(cat),
                                "chars": len(text), "text": text})

    complete &= ctx.run_items("decisions", sorted(st["cases"]), decision, workers=1)
    return complete
