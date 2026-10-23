"""audit.mn (National Audit Office) — every published audit report (title, audit office, audit type, detail link) and
the opinion type of financial-statement audits (unmodified / qualified / adverse / disclaimer) via the site's own
filter. Report PDFs are not downloaded. Reachable only from Mongolian networks."""
import re

from .core import Http, log, strip_tags

BASE = "https://www.audit.mn/audit"
OPINIONS = ["Өөрчлөлтгүй дүгнэлт", "Хязгаарлалтай дүгнэлт", "Сөрөг дүгнэлт", "Татгалзсан дүгнэлт"]
PAGE = 20
KEYS = {"audits": ["id"], "audit_opinions": ["id", "opinion"]}
http = Http(rate=1.0, timeout=180, retries=8)


def list_page(offset, opinion=""):
    params = {"q": "", "cat": 0, "aimag_id": 0, "year": "", "audit_report_type": 0, "audit_category": 0,
              "audit_opinion_type": opinion}
    html = http.get(f"{BASE}/{offset}", params=params).text
    total = re.search(r"Нийт\s*<b[^>]*>\s*([\d,\s]+)", html)
    rows = []
    for href, tr in re.findall(r'<tr[^>]*clickable-row[^>]*data-href="([^"]+)"[^>]*>(.*?)</tr>', html, re.S):
        m = re.search(r"report_detail/(\d+)", href)
        if not m:
            continue
        cells = [strip_tags(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        cells = [c for c in cells if c and c != "-->"]
        rows.append({"id": int(m.group(1)), "url": href.strip(), "cells": cells})
    return (int(re.sub(r"\D", "", total.group(1))) if total else None), rows


def crawl(ctx):
    st = ctx.state
    if "total" not in st:
        st["total"], _ = list_page(0)
        st["opinion_totals"] = {o: list_page(0, o)[0] or 0 for o in OPINIONS}
        ctx.save()
    ctx.expect("audits", st["total"])
    for o, n in st["opinion_totals"].items():
        ctx.expect(f"audit_opinions:{o}", n)
    log(f"{st['total']} reports; opinions: {st['opinion_totals']}")

    def page(key):
        opinion, offset = key.split("|")
        _, rows = list_page(int(offset), opinion)
        if not rows:
            raise RuntimeError("empty page")
        for r in rows:
            if opinion:
                ctx.write("audit_opinions", {"id": r["id"], "opinion": opinion})
            else:
                c = r["cells"]
                ctx.write("audits", {"id": r["id"], "url": r["url"], "no": c[0] if c else None,
                                     "title": c[1] if len(c) > 1 else None, "office": c[2] if len(c) > 2 else None,
                                     "audit_type": c[3] if len(c) > 3 else None, "cells": " | ".join(c)})

    keys = [f"|{o}" for o in range(0, st["total"], PAGE)]
    for op, n in st["opinion_totals"].items():
        keys += [f"{op}|{o}" for o in range(0, n, PAGE)]
    return ctx.run_items("pages", keys if not ctx.sample else keys[:2] + keys[-1:], page, workers=2)
