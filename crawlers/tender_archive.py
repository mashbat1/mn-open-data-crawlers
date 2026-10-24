"""Public opendata.mn dataset 193: an archived Tender invitation extract.

This source deliberately has a separate name from the live Tender API.
It cannot establish current or nationwide procurement completeness.
"""
from collections import Counter
import hashlib
import json

from .core import Http, flat, log

BASE = "https://opendata.mn"
DATASET = 193
PAGE = BASE + "/database/29/193"
API = BASE + "/apiv1/file/193?fileType=json&section=1"
META = BASE + "/apiv1/dataset-file-static/193"
KEYS = {"invitations": ["archive_row"], "dataset": ["id"]}
http = Http(rate=0.125, timeout=240, retries=2)


def unwrap(body):
    return body.get("result") if isinstance(body, dict) and "result" in body else body


def validate_rows(meta, body):
    rows = unwrap(body)
    expected = (meta.get("quality") or {}).get("rows_count")
    if not isinstance(expected, int) or expected <= 0:
        raise RuntimeError("Tender archive metadata lacks a positive row count")
    if not isinstance(rows, list) or len(rows) != expected:
        raise RuntimeError(f"Tender archive count mismatch: expected {expected}")
    dictionary = {d["col_name"]: d["name"] for d in meta.get("DataDictionary") or []}
    fields = {key: dictionary.get(key, key) for key in ("invitation_id", "year", "publish_date")}
    ids, years, dates = set(), Counter(), []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Tender archive has a non-object row")
        values = {key: row.get(label, row.get(key)) for key, label in fields.items()}
        if any(v in (None, "") for v in values.values()):
            raise RuntimeError("Tender archive row lacks invitation ID, year or publication date")
        rid = str(values["invitation_id"])
        if rid in ids:
            raise RuntimeError("Tender archive repeats a primary invitation ID")
        ids.add(rid)
        years[str(values["year"])] += 1
        dates.append(str(values["publish_date"]))
    return rows, {"years": dict(sorted(years.items())), "publish_date_min": min(dates),
                  "publish_date_max": max(dates), "unique_invitation_ids": len(ids)}


def crawl(ctx):
    if ctx.stage_done("archive"):
        return True
    meta = unwrap(http.json(META))
    if not isinstance(meta, dict) or meta.get("id") != DATASET or meta.get("published") is not True:
        raise RuntimeError("Unexpected or unpublished Tender archive metadata")
    ctx.write("dataset", flat(meta))
    if ctx.sample:
        log("Tender archive sample checks metadata only; the full run retrieves all rows")
        return True
    response = http.get(API)
    rows, scope = validate_rows(meta, response.json())
    after = unwrap(http.json(META))
    if after.get("updated_at") != meta.get("updated_at") or after.get("quality") != meta.get("quality"):
        raise RuntimeError("Tender archive changed while downloading; retry a consistent snapshot")
    ctx.expect("invitations", len(rows))
    ctx.state["coverage_scope"] = {
        "method": "published archive API", "dataset_id": DATASET, "dataset_page": PAGE,
        "api_url": API, "original_data_source": meta.get("data_source"),
        "archive_publisher": (meta.get("OrganizationModel") or {}).get("name"),
        "archive_updated_at": meta.get("updated_at"), "license": meta.get("LicenseModel"),
        "response_sha256": hashlib.sha256(response.content).hexdigest(),
        "nationwide_completeness_verified": False, "current_2026_coverage_verified": False,
        "attachments_downloaded": False, **scope,
    }
    for number, row in enumerate(rows, 1):
        ctx.write("invitations", {"archive_row": number, **row})
    ctx.mark_stage("archive")
    log("Tender archive verified:", json.dumps(scope, ensure_ascii=False))
    return True
