"""Treasury e-payments (ЕЗА) as published on shilendans.gov.mn: supplier totals per month (each checked against the
server's count), then for every supplier and year the organisations that paid it. The yearly supplier list is not
used: its reported total does not match what its pages return; yearly figures are the sum of the months."""
import datetime as dt
import json
import re
from pathlib import Path
import gzip
import subprocess
import hashlib
import sys

from .core import Http, flat, log
from .shilendans_common import INTEG, pages_all

SUPPLIERS = INTEG + "/integrationECopyCredits/getBeltgenNiiluulegch"
SUPPLIER_ORGS = INTEG + "/integrationECopyCredits/getSupplierOne"
FIRST_YEAR = 2023
KEYS = {"eza_suppliers": ["period", "register", "name"], "eza_supplier_orgs": ["period", "register", "org_name"]}
http = Http(rate=2.0, timeout=30, retries=1)
ROW_FILTERS = {"eza_suppliers": lambda row: bool(re.fullmatch(r"\d{4}-\d{2}", row.get("period", "")))}


def row_filters(state):
    identities = {period: {tuple(key) for key in keys}
                  for period, keys in state.get("reconciled_period_keys", {}).items()}
    def keep(row):
        period = row.get("period", "")
        return ROW_FILTERS["eza_suppliers"](row) and (
            period not in identities or (str(row.get("register") or ""), row.get("name") or "") in identities[period])
    return {"eza_suppliers": keep}


def reconciled_period(workdir, period):
    folder = Path(workdir) / "monthly_reconciliation" / period
    proof_path = folder / "proof.json"
    if not proof_path.exists():
        return None
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if not proof.get("verified"):
        return None
    encoded = (folder / "reconciled_rows.json").read_bytes()
    if hashlib.sha256(encoded).hexdigest() != proof.get("rows_sha256"):
        raise RuntimeError(f"{period}: reconciled rows checksum mismatch")
    rows = json.loads(encoded)
    keys = {(str(row.get("register") or ""), row.get("name") or "") for row in rows}
    transaction_total = proof.get("reported_transaction_total", max(proof.get("reported_totals", [0])))
    if (proof.get("period") != period or len(keys) != len(rows) or len(rows) != proof.get("expected")
            or sum(int(row["count"]) for row in rows) != transaction_total
            or not proof.get("terminal_short_page_verified") or proof.get("baseline_mismatches") != 0):
        raise RuntimeError(f"{period}: invalid independent monthly reconciliation")
    return rows, proof


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
            proof = state.get("reconciled_period_proofs", {}).get(directory.name)
            if not proof or not proof.get("verified") or directory.name not in state.get("reconciled_period_keys", {}):
                raise RuntimeError(f"{directory.name}: conflicting source totals {sorted(totals)}")
            if reconciled_period(workdir, directory.name) is None:
                raise RuntimeError(f"{directory.name}: reconciliation evidence is unavailable")


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
    if not st.get("monthly_integrity_version"):
        counts = {}
        files = list((Path(ctx.workdir) / "monthly_bootstrap").glob("*.gz")) + list(Path(ctx.parts).glob("eza_suppliers.*.gz"))
        for path in files:
            with gzip.open(path, "rt", encoding="utf-8") as source:
                for line in source:
                    row = json.loads(line)
                    if ROW_FILTERS["eza_suppliers"](row):
                        counts.setdefault(row["period"], set()).add((str(row.get("register") or ""), row.get("name") or ""))
        repair = {period for period, keys in counts.items()
                  if len(keys) != st.get("expect", {}).get("eza_suppliers:" + period)}
        repair.update(key for key in st.get("errors", {}).get("periods", {}) if len(key) == 7)
        st["monthly_integrity_periods"] = sorted(repair)
        for period in repair:
            ctx.done_set("periods").discard(period)
            st.setdefault("attempts", {}).setdefault("periods", {})[period] = 0
        st["monthly_integrity_version"] = 1
        log("monthly identity reconciliation:", sorted(repair))
    plist = periods()

    def one_period(p):
        period, start, end = p
        params = {"startDate": start, "endDate": end, "supplierType": "", "ezaType": "", "registerNo": "", "orgName": ""}
        def progress(n, total):
            if n % 1000 == 0 or n == total:
                log(f"{period}: {n}/{total} supplier rows")
        recovered = reconciled_period(ctx.workdir, period)
        if recovered is None and period in st.get("monthly_integrity_periods", []):
            folder = Path(ctx.workdir) / "monthly_reconciliation" / period
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / "runner.log", "w", encoding="utf-8") as output:
                subprocess.run([sys.executable, "-u", "reconcile_eza.py", "--period", period, "--workdir", str(ctx.workdir)],
                               stdout=output, stderr=subprocess.STDOUT, check=True)
            recovered = reconciled_period(ctx.workdir, period)
            if recovered is None:
                raise RuntimeError(f"{period}: independent code and source counts do not reconcile; see proof.json")
        if recovered is not None:
            rows, proof = recovered
            total = proof["expected"]
            st.setdefault("reconciled_period_proofs", {})[period] = proof
            st.setdefault("reconciled_period_keys", {})[period] = [[str(row.get("register") or ""), row.get("name") or ""] for row in rows]
            log(f"{period}: independently verified {total} suppliers, {proof['transactions']} transactions")
        else:
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
                            "annual_totals": "sum of verified monthly supplier lists; legacy annual lists excluded",
                            "monthly_reconciliation": st.get("reconciled_period_proofs", {})}
    return complete
