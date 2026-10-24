"""Treasury e-payments (ЕЗА) as published on shilendans.gov.mn: supplier totals per month (each checked against the
server's count), then for every supplier and year the organisations that paid it. The yearly supplier list is not
used: its reported total does not match what its pages return; yearly figures are the sum of the months."""
import datetime as dt
import json
import re
from pathlib import Path
import gzip
import subprocess

from .core import Http, flat, log
from .shilendans_common import INTEG, pages_all

SUPPLIERS = INTEG + "/integrationECopyCredits/getBeltgenNiiluulegch"
SUPPLIER_ORGS = INTEG + "/integrationECopyCredits/getSupplierOne"
FIRST_YEAR = 2023
KEYS = {"eza_suppliers": ["period", "register", "name"], "eza_supplier_orgs": ["period", "register", "org_name"]}
http = Http(rate=2.0, timeout=30, retries=1)
ROW_FILTERS = {"eza_suppliers": lambda row: bool(re.fullmatch(r"\d{4}-\d{2}", row.get("period", "")))}


def registers_from_parts(paths):
    years = {}
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            for line in source:
                row = json.loads(line)
                if ROW_FILTERS["eza_suppliers"](row) and row.get("register"):
                    years.setdefault(row["period"][:4], set()).add(str(row["register"]))
    return {year: sorted(regs) for year, regs in years.items()}


def validate_snapshot(state, workdir):
    if state.get("monthly_union_version") != 1:
        raise RuntimeError("Supplier-year inputs have not been rebuilt from all monthly records")
    for directory in (Path(workdir) / "monthly_pages").glob("*"):
        totals = {json.loads(p.read_text(encoding="utf-8"))["meta"]["totalElements"]
                  for p in directory.glob("page_*.json")}
        if len(totals) > 1:
            raise RuntimeError(f"{directory.name}: conflicting source totals {sorted(totals)}")


def periods():
    today = dt.date.today()
    out = []
    for y in range(FIRST_YEAR, today.year + 1):
        for m in range(1, 13):
            first = dt.date(y, m, 1)
            if first > today:
                break
            last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
            out.append((f"{y}-{m:02d}", first.isoformat(), last.isoformat()))
    return out


def crawl(ctx):
    st = ctx.state
    if st.get("monthly_period_version", 0) < 4:
        st["monthly_period_version"] = 4
        obsolete = {k: v for k, v in st.get("errors", {}).get("periods", {}).items() if len(k) == 4}
        st.setdefault("superseded_annual_period_errors", {}).update(obsolete)
        for key in obsolete:
            st["errors"]["periods"].pop(key)
            st.get("attempts", {}).get("periods", {}).pop(key, None)
        for key in st.get("errors", {}).get("periods", {}):
            st["attempts"].setdefault("periods", {})[key] = 0
        for directory in (Path(ctx.workdir) / "monthly_pages").glob("*"):
            totals = {json.loads(p.read_text(encoding="utf-8"))["meta"]["totalElements"]
                      for p in directory.glob("page_*.json")}
            if len(totals) > 1:
                ctx.done_set("periods").discard(directory.name)
        st.get("errors", {}).pop("snapshot_validation", None)
    if st.get("monthly_union_version") != 1:
        if ctx.done_set("periods"):
            folder = Path(ctx.workdir) / "monthly_bootstrap"
            folder.mkdir(exist_ok=True)
            subprocess.run(["gh", "release", "download", "wip-shilendans_eza", "-R", "mashbat1/gov-crawler",
                            "-p", "eza_suppliers.*.jsonl.gz", "-D", str(folder), "--clobber"], check=True)
            rebuilt = registers_from_parts(list(folder.glob("*.gz")) + list(Path(ctx.parts).glob("eza_suppliers.*.gz")))
            if not rebuilt:
                raise RuntimeError("No monthly supplier records available for input reconciliation")
            st["year_regs"] = rebuilt
            log("rebuilt monthly supplier registers:", {year: len(regs) for year, regs in rebuilt.items()})
        st["monthly_union_version"] = 1
    st.setdefault("year_regs", {})
    plist = periods()

    def one_period(p):
        period, start, end = p
        params = {"startDate": start, "endDate": end, "supplierType": "", "ezaType": "", "registerNo": "", "orgName": ""}
        def progress(n, total):
            if n % 1000 == 0 or n == total:
                log(f"{period}: {n}/{total} supplier rows")
        rows, total = pages_all(http, SUPPLIERS, params, size=100, on_page=progress,
                                cache_dir=Path(ctx.workdir) / "monthly_pages" / period)
        if total is not None and len(rows) < total:
            raise RuntimeError(f"{period}: {len(rows)} of {total}")
        ctx.expect(f"eza_suppliers:{period}", total if total is not None else len(rows))
        for r in rows:
            ctx.write("eza_suppliers", {"period": period, "register": r.get("register") or "", "name": r.get("name") or "",
                                        "count": r.get("count"), "amount": r.get("amount"),
                                        "json": json.dumps(r, ensure_ascii=False)})
        regs = st["year_regs"].setdefault(period[:4], [])
        regs.extend(sorted({str(r.get("register")) for r in rows if r.get("register")} - set(regs)))

    complete = ctx.run_items("periods", plist if not ctx.sample else plist[-2:], one_period, key=lambda p: p[0], workers=2)
    items = [f"{y}:{reg}" for y, regs in sorted(st["year_regs"].items()) for reg in regs]
    log(f"supplier→organisation: {len(items)} supplier-years")

    def one_supplier(key):
        y, reg = key.split(":")
        rows, total = pages_all(http, SUPPLIER_ORGS, {"startDate": f"{y}-01-01", "endDate": f"{y}-12-31", "registerNo": reg}, size=200)
        if total is not None and len(rows) < total:
            raise RuntimeError(f"{key}: {len(rows)} of {total}")
        for r in rows:
            ctx.write("eza_supplier_orgs", {"period": y, "register": reg, "org_name": r.get("name") or "",
                                            "count": r.get("transactionCount"), "amount": r.get("transactionAmount"),
                                            "json": json.dumps(r, ensure_ascii=False)})

    complete &= ctx.run_items("supplier_orgs", items, one_supplier, workers=4)
    if complete:
        ctx.expect("eza_suppliers", sum(st["expect"][f"eza_suppliers:{period}"] for period, _, _ in plist))
    st["coverage_scope"] = {"supplier_periods": [p[0] for p in plist],
                            "annual_totals": "sum of verified monthly supplier lists; legacy annual lists excluded"}
    return complete
