"""Checkpoint official bulk-export requests partitioned by the complete published organisation directory."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import time

import pyarrow.parquet as pq
from crawlers.core import Http, TimeBudgetExpired
from crawlers.shilendans_common import REPORT, REPORTS, pages_all

REPO = "mashbat1/gov-crawler"


def gh(*args, check=True):
    run = subprocess.run(["gh", *args, "-R", REPO], text=True, capture_output=True)
    if check and run.returncode:
        raise RuntimeError("Private repair checkpoint operation failed: " + run.stderr[:250])
    return run


def cached_export(http, body, ids, path):
    """Split failed large requests; cache successful leaves and preserve every returned row."""
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as inp:
            return json.load(inp)
    try:
        rows = http.json(REPORTS + "/openData1", method="POST", json={**body, "orgs": ids})
        if not isinstance(rows, list):
            raise RuntimeError("Bulk export returned a non-list response")
    except TimeBudgetExpired:
        raise
    except RuntimeError:
        if len(ids) <= 1:
            raise
        middle = len(ids) // 2
        rows = (cached_export(http, body, ids[:middle], path.with_name(path.stem + "_a.gz"))
                + cached_export(http, body, ids[middle:], path.with_name(path.stem + "_b.gz")))
    with gzip.open(path, "wt", encoding="utf-8") as out:
        json.dump(rows, out, ensure_ascii=False)
    return rows


def collect_partitions(base_tag, failed, root, budget_minutes=65):
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    repair_tag = "wip-od-repair-" + hashlib.sha256(base_tag.encode()).hexdigest()[:12]
    exists = gh("release", "view", repair_tag, check=False).returncode == 0
    if exists:
        gh("release", "download", repair_tag, "-D", str(cache), "--clobber")
    else:
        gh("release", "create", repair_tag, "--title", "Shilendans failed-month repair checkpoints",
           "--notes", "Incomplete repair; successful partitions are preserved. No full-coverage claim.",
           "--prerelease", "--latest=false")
    progress_path = cache / "progress.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {"base_release": base_tag, "runs": 0}
    if progress["base_release"] != base_tag:
        raise RuntimeError("Repair checkpoint belongs to another base snapshot")
    progress["runs"] += 1
    if not (cache / "org_ids.json").exists():
        # This fresh directory snapshot was reconciled to its official server total.
        directory_tag = "shilendans_reports-2026-10-24"
        gh("release", "download", directory_tag, "-p", "shilendans_reports__orgs.parquet",
           "-p", "manifest.json", "-D", str(cache), "--clobber")
        metadata = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
        values = pq.read_table(cache / "shilendans_reports__orgs.parquet", columns=["id"]).column("id").to_pylist()
        ids = sorted(set(values))
        expected = metadata.get("server_reported", {}).get("orgs")
        if not isinstance(expected, int) or len(ids) != expected or None in ids:
            raise RuntimeError("Organisation directory does not reconcile to the source total")
        (cache / "org_ids.json").write_text(json.dumps(ids), encoding="utf-8")
        progress["organisation_directory_release"] = directory_tag
        progress["organisation_count"] = len(ids)
    ids = json.loads((cache / "org_ids.json").read_text())
    started = time.monotonic()
    http = Http(rate=0.5, timeout=90, retries=0)
    http.deadline = started + budget_minutes * 60
    updates = {}
    paused = False
    error = None
    try:
        if "report_group_ids" not in progress:
            groups, total = pages_all(http, REPORT + "/reportGroups", {"sort": "id,asc"}, size=500)
            if total is not None and len(groups) != total:
                raise RuntimeError("Report group catalogue is incomplete")
            progress["report_group_ids"] = [g["id"] for g in groups]
        for task in failed:
            fid, year, month = map(int, task.split(":"))
            form = http.json(f"{REPORT}/reportForms/{fid}")
            body = {"reportFormId": fid, "columns": [h["id"] for h in form.get("reportFormHeaders") or []],
                    "years": [year], "months": [month],
                    "reportGroupIds": progress["report_group_ids"], "tez": [], "ttz": []}
            records = []
            for index, offset in enumerate(range(0, len(ids), 250)):
                rows = cached_export(http, body, ids[offset:offset + 250], cache / f"p_{fid}_{year}_{month}_{index:04}.json.gz")
                records.extend(rows)
                if index % 25 == 0:
                    print(f"{task}: {index + 1} organisation batches checked", flush=True)
            updates[(fid, year, month)] = [
                {"form_id": fid, "year": year, "month": month, "seq": seq,
                 "org_register": row.get("registerNo"), "org_name": row.get("name"), "line": row.get("line"),
                 "data": json.dumps({k: v for k, v in row.items() if len(k) <= 2 and k != "id" and v not in (None, "")}, ensure_ascii=False)}
                for seq, row in enumerate(records)]
            print(f"{task}: all {len(ids)} directory organisations checked; {len(records)} rows", flush=True)
    except TimeBudgetExpired:
        paused = True
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        progress.update(paused=paused, error=error, cached_partitions=len(list(cache.glob("p_*.gz"))))
        progress_path.write_text(json.dumps(progress, indent=2), encoding="utf-8")
        assets = [p for p in cache.iterdir() if p.name in {"org_ids.json", "progress.json"} or p.suffix == ".gz"]
        for offset in range(0, len(assets), 20):
            gh("release", "upload", repair_tag, *map(str, assets[offset:offset + 20]), "--clobber")
        remote = json.loads(gh("release", "view", repair_tag, "--json", "assets").stdout)["assets"]
        sizes = {a["name"]: a["size"] for a in remote}
        if any(sizes.get(p.name) != p.stat().st_size for p in assets):
            raise RuntimeError("Repair checkpoint upload size mismatch; local runner copy preserved")
    if paused:
        (root / "CONTINUE").touch()
        return None, progress, repair_tag
    if error:
        raise RuntimeError(error + "; successful partitions preserved in " + repair_tag)
    return updates, progress, repair_tag
