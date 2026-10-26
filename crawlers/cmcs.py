"""cmcs.mrpam.gov.mn - the mineral and petroleum licence cadastre (УУККС, public, no login): every licence the
public grid lists (it shows valid licences only) with holder, type, status, area, administrative units,
minerals and boundary polygon, plus the special-area registers (reserves, strategic deposits, areas open for
licensing, artisanal-mining areas, special-need and protected areas).

robots.txt (fetched and enforced first) disallows /CMCS/{Content,Scripts,js,Views,Output,logs,legends,
App_Browsers,App_Data}/ - none of which is requested here. mrpam.gov.mn itself disallows all crawling and is
never contacted. Grant/expiry/fee dates are hidden from anonymous users ("SecureProp") and are null here.
"""
import json
import re

from .core import Http, log
from .opendata_gov import obey_robots

BASE = "https://cmcs.mrpam.gov.mn"
CMCS = BASE + "/CMCS"
GRID_PAGE = 1000
SPECIAL_AREAS = {2: "Эдэлбэр газар", 3: "Нөөцөд авсан газар", 6: "Стратегийн ордын талбай",
                 8: "Тусгай зөвшөөрөл олгох боломжтой талбай", 10: "Бичил уурхайн талбай",
                 11: "Улсын тусгай хэрэгцээний газар", 12: "Орон нутгийн тусгай хэрэгцээний газар",
                 13: "Бусад тусгай газар", 14: "Улсын тусгай хамгаалалттай газар",
                 15: "Орон нутгийн тусгай хамгаалалттай газар"}
LICENSE_COLS = ["id", "code", "name", "type", "status", "holder", "area_ha"]
AREA_COLS = ["id", "code", "name", "status", "purpose", "decided", "effective_date", "expiry_date", "area_ha"]
KEYS = {"cmcs_licenses": ["id"], "cmcs_license_list": ["id"], "cmcs_special_areas": ["index_type", "id"]}
http = Http(rate=1.0, timeout=120, retries=4, headers={"X-Requested-With": "XMLHttpRequest"})


def grid(url, method="GET", extra=None, sample=False):
    """All rows of a jqGrid endpoint (sorted by Id, so pages are stable)."""
    rows, page, records = [], 1, None
    while True:
        params = {"_search": "false", "rows": GRID_PAGE, "page": page, "sidx": "Id", "sord": "asc", **(extra or {})}
        o = (http.json(url, params=params) if method == "GET" else http.json(url, method="POST", data=params))
        records = o["records"]
        rows += o["rows"]
        if sample or page >= o["total"] or not o["rows"]:
            break
        page += 1
    ids = {r["id"] for r in rows}
    if not sample and len(ids) != records:
        raise RuntimeError(f"{url} {extra}: {len(ids)} unique rows, server reports {records}")
    return rows, records


def js_object(src, start):
    """The balanced {...} literal starting at src[start], string-aware."""
    depth, i, quote = 0, start, None
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise ValueError("unbalanced object literal")


def js_to_json(lit):
    """Minified JS object literal (unquoted keys, !0/!1) -> Python object."""
    parts = re.split(r'("(?:[^"\\]|\\.)*")', lit)
    for k in range(0, len(parts), 2):
        p = re.sub(r'([{,]\s*)([A-Za-z_$][\w$]*)\s*:', r'\1"\2":', parts[k])
        parts[k] = re.sub(r'!0\b', "true", re.sub(r'!1\b', "false", re.sub(r'\bvoid 0\b', "null", p)))
    return json.loads("".join(parts))


def license_model(html):
    m = re.search(r'ModelName:"License"', html)
    if not m:
        raise RuntimeError("licence model not found in page")
    for s in reversed([x.start() + 1 for x in re.finditer(r"=\{", html[:m.start()])]):
        lit = js_object(html, s)
        if s + len(lit) > m.end():
            return js_to_json(lit)
    raise RuntimeError("licence model literal not found")


def hidden(v):
    return None if v in ("SecureProp", "") else v


def license_row(lic_id, o):
    h = o.get("HolderLookup") or {}
    secure = [k for k in ("GrantDate", "ExpiryDate", "FeeDueDate") if o.get(k) == "SecureProp"]
    dump = lambda v: json.dumps(v, ensure_ascii=False) if v is not None else None  # noqa: E731
    return {"id": lic_id, "code": o.get("Code"), "name": o.get("Name"), "type": o.get("TypeName"),
            "status": o.get("StatusName"), "mining_type": o.get("MiningTypeName"),
            "deposit_type": o.get("DepositTypeName"), "holder_id": h.get("id"), "holder_name": h.get("Name"),
            "holder_register": h.get("RegistrationNumber"), "holder_registration_id": h.get("RegistrationId"),
            "holder_type": h.get("Type"), "holder_group": (o.get("HolderGroup") or {}).get("Name"),
            "grant_date": hidden(o.get("GrantDate")), "expiry_date": hidden(o.get("ExpiryDate")),
            "fee_due_date": hidden(o.get("FeeDueDate")), "hidden_fields": ",".join(secure) or None,
            "area_ha": o.get("Area"), "radioactive": o.get("Radioactive"), "rare_earth": o.get("RareEarth"),
            "pending_transaction": o.get("HasPendingTransaction"), "pending_court_case": o.get("HasPendingCourtCase"),
            "pending_criminal_investigation": o.get("HasPendingCriminalInvestigation"),
            "admin_units": dump([{"type": a.get("TypeName"), "name": a.get("Name"), "id": a.get("Id")}
                                 for a in o.get("AdminUnits") or []]),
            "minerals": dump(o.get("Minerals")), "pledgees": dump(o.get("Pledgees")),
            "transactions": dump(o.get("Transactions")), "holder": dump(o.get("Holder")),
            "geometry": dump(o.get("Geometry")), "source_url": f"{CMCS}/License/Details/{lic_id}"}


def crawl(ctx):
    st = ctx.state
    obey_robots(http, BASE, ["/CMCS/License/GridData", "/CMCS/License/Details/1", "/CMCS/SpecialArea/GridData"])
    complete = True
    if not ctx.stage_done("license_list"):
        rows, records = grid(f"{CMCS}/License/GridData", sample=ctx.sample)
        ctx.expect("cmcs_licenses", records)
        for r in rows:
            ctx.write("cmcs_license_list", dict(zip(LICENSE_COLS, r["cell"])))
        st["license_ids"] = sorted(r["id"] for r in rows)
        log(f"licence grid: {len(rows)} of {records}")
        ctx.mark_stage("license_list")

    def detail(lic_id):
        html = http.get(f"{CMCS}/License/Details/{lic_id}").text
        ctx.write("cmcs_licenses", license_row(int(lic_id), license_model(html)))

    complete &= ctx.run_items("details", st["license_ids"], detail, workers=2)

    def areas(index_type):
        rows, records = grid(f"{CMCS}/SpecialArea/GridData", "POST", {"indexType": index_type}, sample=ctx.sample)
        ctx.expect(f"cmcs_special_areas:{index_type}", records)
        for r in rows:
            ctx.write("cmcs_special_areas", {"index_type": index_type, "register": SPECIAL_AREAS[index_type],
                                             **dict(zip(AREA_COLS, r["cell"]))})

    complete &= ctx.run_items("special_areas", list(SPECIAL_AREAS), areas, workers=1)
    return complete
