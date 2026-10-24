"""Cross-check a problematic monthly supplier list against every economic code exposed by the public UI."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from crawlers.core import Http
from crawlers.shilendans_common import INTEG

ROOT = Path("work_shilendans_eza/monthly_reconciliation/2026-05")
PARAMS = {"startDate": "2026/05/01", "endDate": "2026/05/31", "supplierType": "",
          "registerNo": "", "orgName": ""}


def merge_groups(groups):
    merged = {}
    for rows in groups:
        for row in rows:
            key = (str(row.get("register") or ""), row.get("name") or "")
            if not any(key) or row.get("amount") is None or row.get("count") is None:
                raise RuntimeError("Cannot aggregate a supplier with unknown identity, amount or count")
            if key not in merged:
                merged[key] = {**row, "amount": Decimal(0), "count": 0}
            merged[key]["amount"] += Decimal(str(row["amount"]))
            merged[key]["count"] += int(row["count"])
    for row in merged.values():
        amount = row["amount"]
        row["amount"] = int(amount) if amount == amount.to_integral_value() else str(amount)
    return list(merged.values())


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    http = Http(rate=0.5, timeout=30, retries=1)
    codes = http.json(INTEG + "/integrationECopyCredits/eza", params=PARAMS)
    if not isinstance(codes, list) or not codes or any(not c.get("code") for c in codes):
        raise RuntimeError("Invalid public economic-code catalogue")
    values = sorted({str(c["code"]) for c in codes})
    groups, raw = [], 0
    query_failures = []
    def collect(code, register=""):
        rows, page = [], 0
        while True:
            suffix = "_reg_" + register if register else ""
            path = ROOT / f"code_{code}{suffix}_page_{page:04}.json"
            if path.exists():
                body = json.loads(path.read_text(encoding="utf-8"))
            else:
                try:
                    body = http.json(INTEG + "/integrationECopyCredits/getBeltgenNiiluulegch",
                                     params={**PARAMS, "ezaType": code, "registerNo": register, "page": page, "size": 100})
                except RuntimeError as exc:
                    query_failures.append({"code": code, "register_filter": register, "page": page, "error": str(exc)})
                    return rows
                path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            chunk = body.get("content")
            if not isinstance(chunk, list):
                raise RuntimeError("Invalid public supplier page")
            rows.extend(chunk)
            if len(chunk) < 100:
                return rows
            page += 1
            if page >= 1000:
                raise RuntimeError("Economic-code pagination did not terminate")
    for number, code in enumerate(values, 1):
        rows = collect(code)
        identities = [(r.get("register"), r.get("name")) for r in rows]
        if len(set(identities)) != len(identities) or any(e["code"] == code for e in query_failures):
            # Native amount sorting has unstable ties. The UI's register filter
            # supplies independent overlapping views; merge identical aggregates.
            by_identity = {(r.get("register"), r.get("name")): r for r in rows}
            for digit in "0123456789":
                for row in collect(code, digit):
                    key = (row.get("register"), row.get("name"))
                    old = by_identity.get(key)
                    if old and (old["count"] != row["count"] or Decimal(str(old["amount"])) != Decimal(str(row["amount"]))):
                        raise RuntimeError("Conflicting same-code supplier aggregates")
                    by_identity[key] = row
            rows = list(by_identity.values())
        groups.append(rows)
        raw += len(rows)
        print(f"economic codes {number}/{len(values)}: {raw} grouped supplier rows", flush=True)
    merged = merge_groups(groups)
    base_folder = ROOT.parents[1] / "monthly_pages/2026-05"
    baseline = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(base_folder.glob("page_*.json"))]
    reported_totals = {page["meta"]["totalElements"] for page in baseline}
    expected = min(reported_totals)
    transaction_total = max(reported_totals)
    actual_transactions = sum(row["count"] for row in merged)
    by_key = {(str(r.get("register") or ""), r.get("name") or ""): r for r in merged}
    mismatches = 0
    for page in baseline:
        for row in page["rows"]:
            found = by_key.get((str(row.get("register") or ""), row.get("name") or ""))
            if (not found or found["count"] != row["count"] or
                    abs(Decimal(str(found["amount"])) - Decimal(str(row["amount"]))) > Decimal("0.01")):
                mismatches += 1
    proof = {"period": "2026-05", "expected": expected, "unique_suppliers": len(merged),
             "reported_totals": sorted(reported_totals), "transactions": actual_transactions,
             "public_economic_codes": values, "baseline_mismatches": mismatches,
             "query_failures": query_failures,
             "verified": len(merged) == expected and actual_transactions == transaction_total and mismatches == 0,
             "method": "sum of every economic code exposed by the official public UI"}
    encoded = json.dumps(merged, ensure_ascii=False)
    proof["rows_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    (ROOT / "reconciled_rows.json").write_text(encoded, encoding="utf-8")
    (ROOT / "proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof), flush=True)


if __name__ == "__main__":
    main()
