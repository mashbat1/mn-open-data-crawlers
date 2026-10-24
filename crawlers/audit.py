"""audit.mn (National Audit Office) — every published audit report (title, audit office, audit type, detail link) and
the opinion type of financial-statement audits (unmodified / qualified / adverse / disclaimer) via the site's own
filter. Report PDFs are not downloaded. Reachable only from Mongolian networks."""
import re
import gzip
import glob
import json
import os
from collections import Counter
import datetime as dt

from .core import Http, TimeBudgetExpired, log, strip_tags

BASE = "https://www.audit.mn/audit"
OPINIONS = ["Өөрчлөлтгүй дүгнэлт", "Хязгаарлалтай дүгнэлт", "Сөрөг дүгнэлт", "Татгалзсан дүгнэлт"]
PAGE = 20
KEYS = {"audits": ["id"], "audit_opinions": ["id", "opinion"]}
http = Http(rate=1.0, timeout=180, retries=8)


def list_page(offset, opinion="", **filters):
    params = {"q": "", "cat": 0, "aimag_id": 0, "year": "", "audit_report_type": 0, "audit_category": 0,
              "audit_opinion_type": opinion, **filters}
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
        if st["total"] is None:
            raise RuntimeError("audit list did not expose its total")
        ctx.save()
        log(f"audit: {st['total']} reports")
    st.setdefault("opinion_totals", {})
    for opinion in OPINIONS:
        if opinion not in st["opinion_totals"]:
            count, _ = list_page(0, opinion)
            if count is None:
                raise RuntimeError(f"audit opinion list did not expose its total: {opinion}")
            st["opinion_totals"][opinion] = count
            ctx.save()
            log(f"audit: {opinion}: {count}")
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
                st.setdefault("opinion_ids", {}).setdefault(opinion, {})[str(r["id"])] = True
                ctx.write("audit_opinions", {"id": r["id"], "opinion": opinion})
            else:
                c = r["cells"]
                st.setdefault("audit_ids", {})[str(r["id"])] = c[2] if len(c) > 2 else None
                ctx.write("audits", {"id": r["id"], "url": r["url"], "no": c[0] if c else None,
                                     "title": c[1] if len(c) > 1 else None, "office": c[2] if len(c) > 2 else None,
                                     "audit_type": c[3] if len(c) > 3 else None, "cells": " | ".join(c)})

    keys = [f"|{o}" for o in range(0, st["total"], PAGE)]
    for op, n in st["opinion_totals"].items():
        keys += [f"{op}|{o}" for o in range(0, n, PAGE)]
    complete = ctx.run_items("pages", keys if not ctx.sample else keys[:2] + keys[-1:], page, workers=2)
    if not ctx.sample:
        complete &= reconcile(ctx)
    return complete


def reconcile(ctx):
    """The global list swaps tied rows between pages. Reconcile by the site's public office/type filters."""
    st = ctx.state
    if st.get("reconcile_version", 0) < 3:
        st["reconcile_version"] = 3
        st["reconcile_v3_prior_errors"] = dict(st.get("errors", {}).get("reconcile", {}))
        for key in st["reconcile_v3_prior_errors"]:
            st["attempts"].setdefault("reconcile", {})[key] = 0
    known = st.setdefault("audit_ids", {})
    opinions = st.setdefault("opinion_ids", {})
    if not st.get("reconcile_bootstrapped"):
        for path in glob.glob(os.path.join(ctx.parts, "*.jsonl.gz")):
            # Existing sinks may still be open on a later resume; their new rows
            # are tracked below, while completed prior-run parts are importable.
            if any(s.f and s.f.name == path for s in ctx.sinks.values()):
                continue
            if os.path.basename(path).startswith("audits."):
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    for line in fh:
                        row = json.loads(line)
                        known[str(row["id"])] = row.get("office")
            elif os.path.basename(path).startswith("audit_opinions."):
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    for line in fh:
                        row = json.loads(line)
                        opinions.setdefault(row["opinion"], {})[str(row["id"])] = True
        st["reconcile_bootstrapped"] = True
    if "offices" not in st:
        html = http.get(BASE + "/0").text
        select = re.search(r'<select[^>]*name="aimag_id"[^>]*>(.*?)</select>', html, re.S)
        if not select:
            raise RuntimeError("audit office filter not found")
        st["offices"] = {value: strip_tags(label) for value, label in
                         re.findall(r'<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', select.group(1), re.S) if value != "0"}
        ctx.save()

    def store(rows, opinion):
        for row in rows:
            rid = str(row["id"])
            cells = row["cells"]
            if rid not in known:
                known[rid] = cells[2] if len(cells) > 2 else None
                ctx.write("audits", {"id": row["id"], "url": row["url"], "no": cells[0] if cells else None,
                                     "title": cells[1] if len(cells) > 1 else None, "office": known[rid],
                                     "audit_type": cells[3] if len(cells) > 3 else None, "cells": " | ".join(cells)})
            if opinion and rid not in opinions.setdefault(opinion, {}):
                opinions[opinion][rid] = True
                ctx.write("audit_opinions", {"id": row["id"], "opinion": opinion})

    def one(key):
        opinion, office = key.split("|")
        name = st["offices"][office]

        def have():
            ids = opinions.get(opinion, {}) if opinion else known
            return sum(known.get(rid) == name for rid in ids)

        expected, rows = list_page(0, opinion, aimag_id=office)
        if expected is None:
            raise RuntimeError("audit office did not expose a total")
        store(rows, opinion)
        if have() >= expected:
            return
        verified = st.setdefault("verified_audit_partitions", {})
        dimensions = [("year", range(dt.date.today().year, 1999, -1)),
                      ("audit_category", (1, 2, 3, 4, 5, 6)),
                      ("audit_report_type", (1, 2, 3, 5)),
                      ("cat", (10007, 10008, 10009))]

        def partition(query, depth=0):
            if have() >= expected:
                return
            if ctx.time_up():
                raise TimeBudgetExpired()
            token = json.dumps([opinion, query], sort_keys=True)
            if token in verified:
                return
            total, first = list_page(0, opinion, **query)
            if total is None:
                raise RuntimeError("audit partition did not expose a total")
            store(first, opinion)
            seen = {str(r["id"]) for r in first}
            if total <= PAGE:
                if len(seen) != total:
                    raise RuntimeError("audit small partition has duplicate or missing rows")
                verified[token] = total
                return
            # Public filters narrow tied-date groups before using unstable paging.
            if depth < len(dimensions):
                field, values = dimensions[depth]
                for value in values:
                    partition({**query, field: value}, depth + 1)
                    if have() >= expected:
                        return
            titles = [r["cells"][1] for r in first if len(r["cells"]) > 1]
            for offset in range(PAGE, total, PAGE):
                if have() >= expected:
                    return
                if ctx.time_up():
                    raise TimeBudgetExpired()
                _, page_rows = list_page(offset, opinion, **query)
                if not page_rows:
                    raise RuntimeError("empty audit partition page before reported total")
                store(page_rows, opinion)
                seen.update(str(r["id"]) for r in page_rows)
                titles.extend(r["cells"][1] for r in page_rows if len(r["cells"]) > 1)
            if len(seen) == total:
                verified[token] = total
            elif "q" not in query:
                # Search terms come from the site's displayed titles. They
                # split residual ties without guessing or enumerating IDs.
                words = Counter(w.lower() for title in titles for w in re.findall(r"[^\W\d_]{4,}", title))
                for word, _ in sorted(words.items(), key=lambda item: (item[1], -len(item[0])))[:24]:
                    partition({**query, "q": word}, len(dimensions))
                    if have() >= expected:
                        return

        partition({"aimag_id": office})
        if have() < expected:
            raise RuntimeError(f"audit office {office}: {have()} unique of {expected}")

    jobs = [f"|{office}" for office in st["offices"]] if len(known) < st["total"] else []
    for opinion, total in st["opinion_totals"].items():
        if len(opinions.get(opinion, {})) < total:
            jobs.extend(f"{opinion}|{office}" for office in st["offices"])
    complete = ctx.run_items("reconcile", jobs, one, workers=2)
    log(f"audit unique coverage: {len(known)}/{st['total']}; opinions: " +
        str({op: (len(opinions.get(op, {})), n) for op, n in st["opinion_totals"].items()}))
    if complete and len(known) < st["total"]:
        raise RuntimeError("audit has unresolved duplicate/missing list rows")
    if complete and any(len(opinions.get(op, {})) < n for op, n in st["opinion_totals"].items()):
        raise RuntimeError("audit has unresolved opinion rows")
    return complete
