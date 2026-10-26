"""frc.mn - Financial Regulatory Commission (Санхүүгийн зохицуулах хороо): licensed entities of every regulated
sector (NBFIs, insurers, securities firms, savings-and-credit co-ops, real-estate brokers, precious-metal
dealers, virtual-asset providers, money lenders) with licence numbers, plus per-entity details, shareholders,
products and branches; licensed individuals (insurance agents, precious-metal and money-lending licensees);
revoked insurance agents; Commission resolutions (тогтоол: licence grants, revocations, suspensions, sanctions)
by sector and year; electronic notices; and every document list on the site (incl. revoked-licence lists).

The site is an Angular app whose public JSON API is https://www.frc.mn:5001/api/ (neither host has a
robots.txt; 1 request/s). Decision PDFs live under https://license.frc.mn/ and site documents under
https://www.frc.mn/ - their URLs are recorded, the PDFs are not downloaded.
"""
import datetime as dt
import hashlib
import html
import json

from .core import Http, log
from .opendata_gov import obey_robots

SITE = "https://www.frc.mn"
API = "https://www.frc.mn:5001/api/"
LICENSE_FILES = "https://license.frc.mn/"
# Licence-type lists of the site's own menu pages (rows with a registration_number field are entities)
ENDPOINTS = ["insurance/" + x for x in ("insurance", "longinsurance", "reinsurance", "insurancebroker", "bankbroker",
                                       "damageassesor", "insuranceactuarian", "auditcompany", "lawcompany",
                                       "apprcompany", "miningexchange", "insreprensentative",
                                       "deactivereprensentative")] + \
            ["securitie/" + x for x in ("secbroker", "dealer", "underwriter", "hoz", "nominal", "fst", "homk", "hiu",
                                       "haab", "hbuts", "custodian", "zereglel", "mining")] + \
            ["frc/nbfi", "realestate/deactive"]
KEYS = {"frc_menu": ["iDs"], "frc_entities": ["list", "registration_number", "license_number", "industry_names"],
        "frc_persons": ["list", "row_hash"], "frc_entity_details": ["registration_number"],
        "frc_shareholders": ["registration_number", "seq"], "frc_products": ["registration_number", "seq"],
        "frc_branches": ["registration_number", "seq"], "frc_decisions": ["sector_id", "year", "row_hash"],
        "frc_notices": ["sector_id", "row_hash"], "frc_documents": ["iDs"]}
http = Http(rate=1.0, timeout=120, retries=4, headers={"Content-Type": "application/json"})


def clean(v):
    return html.unescape(v).strip() if isinstance(v, str) else v


def us_date(v):
    """'10/13/2026 12:00:00 AM' -> '2026-10-13'."""
    try:
        return dt.datetime.strptime(v.split()[0], "%m/%d/%Y").date().isoformat()
    except (AttributeError, ValueError, IndexError):
        return None


def row_hash(r):
    return hashlib.sha1(json.dumps(r, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def get(path, method="GET"):
    o = http.json(API + path, method=method)
    if o is None:
        return []
    return o


def norm(r):
    out = {k: clean(v) for k, v in r.items()}
    for k in ("license_date", "decision_date", "start_date"):
        if out.get(k):
            out[k + "_iso"] = us_date(out[k])
    return out


def plan(menu):
    """Sectors, their resolution years, person lists and document menus, from the site menu."""
    by_id = {m["iDs"]: m for m in menu}

    def sector_of(m):
        while m:
            if m["menutid"] == "sct" and m["menuloc"] == "main":
                return m["iDs"]
            m = by_id.get(m["parentid"])
        return None

    sectors = sorted({m["iDs"] for m in menu if m["menutid"] == "sct" and m["menuloc"] == "main"})
    years = sorted({f"{sector_of(m)}:{m['menuname'].split()[0]}" for m in menu
                    if m["menutid"] == "decision" and sector_of(m) and m["menuname"].split()[0].isdigit()})
    persons = sorted({f"person:{sector_of(m)}:{m['iDs']}" for m in menu if m["menutid"] == "person" and sector_of(m)})
    docs = sorted(m["iDs"] for m in menu if m["menutid"].startswith("doc/"))
    return sectors, years, persons, docs


def crawl(ctx):
    st = ctx.state
    obey_robots(http, SITE, ["/"])
    obey_robots(http, "https://www.frc.mn:5001", ["/api/menu", "/api/frc/entity", "/api/frc/decision"])
    complete = True
    if not ctx.stage_done("menu"):
        menu = get("menu?site=main&lang=mn&status=1")
        for m in menu:
            ctx.write("frc_menu", {k: clean(v) for k, v in m.items()})
        sectors, years, persons, docs = plan(menu)
        st.update(sectors=sectors, years=years, docs=docs,
                  lists=[f"entity:{s}" for s in sectors] + persons + [f"ep:{e}" for e in ENDPOINTS])
        st.setdefault("regs", {})
        ctx.expect("frc_menu", len(menu))
        log(f"menu: {len(menu)} items, sectors {sectors}, {len(years)} sector-years, {len(docs)} document menus")
        ctx.mark_stage("menu")

    def one_list(key):
        kind, _, rest = key.partition(":")
        if kind == "entity":
            rows, name = get(f"frc/entity?id={rest}&otid=0"), f"sector:{rest}"
        elif kind == "person":
            sector, otid = rest.split(":")
            rows, name = get(f"person?id={sector}&otid={otid}"), f"person:{sector}:{otid}"
        else:
            rows, name = get(rest), rest
        ctx.expect(f"frc_list:{name}", len(rows))
        for r in rows:
            r = norm(r)
            if "registration_number" in r:
                ctx.write("frc_entities", {"list": name, **r})
                if r["registration_number"]:
                    st["regs"][str(r["registration_number"])] = r.get("entity_name")
            else:
                ctx.write("frc_persons", {"list": name, "row_hash": row_hash(r), **r})

    complete &= ctx.run_items("lists", st["lists"], one_list, workers=1)

    def decisions(key):
        sector, year = key.split(":")
        rows = get(f"frc/decision?sctId={sector}&decId={year}")
        ctx.expect(f"frc_decisions:{key}", len(rows))
        for r in rows:
            r = norm(r)
            ctx.write("frc_decisions", {"sector_id": int(sector), "year": int(year), "row_hash": row_hash(r),
                                        "file_link": LICENSE_FILES + r["file_url"] if r.get("file_url") else None, **r})

    complete &= ctx.run_items("decisions", st["years"], decisions, workers=1)

    def notices(sector):
        rows = get(f"frc/enotice?id={sector}")
        ctx.expect(f"frc_notices:{sector}", len(rows))
        for r in rows:
            r = norm(r)
            ctx.write("frc_notices", {"sector_id": sector, "row_hash": row_hash(r),
                                      "file_link": LICENSE_FILES + r["file_url"] if r.get("file_url") else None, **r})

    complete &= ctx.run_items("notices", st["sectors"], notices, workers=1)

    def documents(menu_id):
        rows = get(f"document?menuid={menu_id}&site=main&lang=mn")
        ctx.expect(f"frc_documents:{menu_id}", len(rows))
        for r in rows:
            r = {k: clean(v) for k, v in r.items()}
            links = [SITE + "/" + r[k].replace("\\", "/") if r.get(k) and not r[k].startswith("http") else r.get(k)
                     for k in ("fileurl", "fileurl1")]
            ctx.write("frc_documents", {**r, "file_link": links[0], "file_link1": links[1]})

    complete &= ctx.run_items("documents", st["docs"], documents, workers=1)

    def details(reg):
        d = get(f"frc/orgdetails?id={reg}")
        if isinstance(d, dict) and d:
            ctx.write("frc_entity_details", {**norm(d), "registration_number": reg})
        for table, path in (("frc_shareholders", "stakeholder"), ("frc_products", "product"), ("frc_branches", "branch")):
            rows = get(f"tabinfo/{path}?register={reg}", method="POST")
            for seq, r in enumerate(rows if isinstance(rows, list) else []):
                r = norm(r)
                if r.get("file_url"):
                    r["file_link"] = LICENSE_FILES + r["file_url"]
                ctx.write(table, {"registration_number": reg, "seq": seq, **r})

    regs = sorted(st["regs"])
    ctx.expect("frc_entity_registrations", len(regs))
    complete &= ctx.run_items("details", regs, details, workers=2)
    return complete
