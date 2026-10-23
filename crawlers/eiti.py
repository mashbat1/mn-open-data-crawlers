"""EITI Mongolia e-reporting (ereports.eitimongolia.mn): extractive companies per reporting year, their payments to the
state by type, and every table of each company's annual report form (incl. section 17: beneficial owners with the
politically-exposed-person flag). Contact phone numbers and e-mails are dropped. Reachable only from Mongolian networks."""
import json
import re

from .core import Http, log, strip_tags

BASE = "https://ereports.eitimongolia.mn"
YEARS = (2021, 2022, 2023, 2024, 2025)
TAB8 = ("amnat", "oat", "lic_payment", "nuat")
TAB9 = ("on_ttat", "on_uat", "on_tuna", "on_other")
KEYS = {"companies": ["year", "register"], "payments": ["year", "tab", "type", "register"],
        "report_tables": ["year", "register", "table_no", "row_no"]}
http = Http(rate=1.5, timeout=180, retries=8, headers={"X-Requested-With": "XMLHttpRequest"})
PHONE = re.compile(r"(?<!\d)(?:\+?976[\s-]?)?[789]\d{3}[\s-]?\d{4}(?!\d)")
CONTACT = re.compile(r"утас|имэйл|и-мэйл|e-?mail|цахим шуудан|факс", re.I)


def rows_of(html):
    return [[strip_tags(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            for tr in re.findall(r"<tbody[^>]*>.*?</tbody>|<tr[^>]*>(.*?)</tr>", html, re.S) if tr]


def table_rows(html):
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [strip_tags(td) for td in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if any(cells):
            out.append(cells)
    return out


def scrub(cells):
    """Drop personal contact details: a row labelled as phone/e-mail keeps its label only; stray e-mails and phone
    numbers are masked."""
    if cells and CONTACT.search(cells[0] or ""):
        return [cells[0]] + ["(хассан)" for _ in cells[1:]]
    return [PHONE.sub("(хассан)", re.sub(r"\S+@\S+", "(хассан)", c or "")) for c in cells]


def crawl(ctx):
    st = ctx.state
    st.setdefault("registers", {})

    def company_pages(year):
        n, page = 0, 0
        while True:
            html = http.post(f"{BASE}/company-list", params={"page": page}, data={"session_year": year}).text
            rows = [r for r in table_rows(html) if len(r) >= 3 and re.fullmatch(r"\d{7}", r[1] or "")]
            if not rows:
                break
            for r in rows:
                ctx.write("companies", {"year": year, "register": r[1], "name": r[2],
                                        "licences": r[3] if len(r) > 3 else None, "form": r[4] if len(r) > 4 else None,
                                        "aimag": r[5] if len(r) > 5 else None, "soum": r[6] if len(r) > 6 else None,
                                        "director": r[7] if len(r) > 7 else None})
                st["registers"].setdefault(str(year), [])
                if r[1] not in st["registers"][str(year)]:
                    st["registers"][str(year)].append(r[1])
            n += len(rows)
            page += 10
            if ctx.sample:
                break
        ctx.expect(f"companies:{year}", n)

    done = ctx.run_items("company_lists", YEARS if not ctx.sample else YEARS[-1:], company_pages, workers=1)

    def payment_pages(key):
        year, tab, typ = key.split(":")
        page, n = 0, 0
        while True:
            html = http.get(f"{BASE}/report-{tab}", params={"page": page, "type": typ, "session_year": year}).text
            rows = [r for r in table_rows(html) if len(r) >= 3 and re.fullmatch(r"\d{7}", r[1] or "")]
            if not rows:
                break
            for r in rows:
                ctx.write("payments", {"year": int(year), "tab": tab, "type": typ, "register": r[1], "name": r[2],
                                       "amount": r[-1], "cells": json.dumps(r, ensure_ascii=False)})
            n += len(rows)
            page += 10
            if ctx.sample:
                break

    keys = [f"{y}:tab8:{t}" for y in YEARS for t in TAB8] + [f"{y}:tab9:{t}" for y in YEARS for t in TAB9]
    done &= ctx.run_items("payments", keys if not ctx.sample else keys[-1:], payment_pages, workers=1)

    def report(key):
        year, reg = key.split(":")
        html = http.get(f"{BASE}/report-print/{reg}", params={"session_year": year}, timeout=300).text
        # split the form into numbered sections so each table keeps its heading
        heading, t_no = None, 0
        for part in re.split(r"(<table[^>]*>.*?</table>)", html, flags=re.S):
            if not part.startswith("<table"):
                heads = re.findall(r"(\d{1,2}(?:\.\d{1,2})*\.\s*[^<]{3,120})", strip_tags(part))
                if heads:
                    heading = heads[-1].strip()
                continue
            t_no += 1
            for i, cells in enumerate(table_rows(part)):
                ctx.write("report_tables", {"year": int(year), "register": reg, "table_no": t_no, "section": heading,
                                            "row_no": i, "cells": json.dumps(scrub(cells), ensure_ascii=False)})
        if t_no == 0:
            raise RuntimeError("no tables in report")

    keys = [f"{y}:{r}" for y, regs in sorted(st["registers"].items()) for r in regs]
    log(f"{len(keys)} company reports")
    done &= ctx.run_items("reports", keys if not ctx.sample else keys[:2], report, workers=2)
    return done
