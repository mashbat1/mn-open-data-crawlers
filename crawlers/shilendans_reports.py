"""Fills the gaps of the Shilendans report crawl: every organisation, and every published report header whose
creation day has fewer reports in the published snapshot (release data-*) than the server lists. Headers embed
the whole report; its rows go to `cells`, the header (without the bulky form definition) to `reports`."""
import collections
import datetime as dt
import json
import os
import subprocess

import pyarrow.parquet as pq

from .core import Http, flat, log
from .shilendans_common import API, REPORTS, pages_all

ORGS = API + "/gap-organization-service/orgs"
FIRST_DAY = dt.date(2022, 12, 3)
FILTER = "isPublish:true"
KEYS = {"orgs": ["id"], "reports": ["id"], "cells": ["report_id", "line", "col"]}
http = Http(rate=4.0, timeout=300, retries=8)


def snapshot_day_counts(ctx):
    """Reports per creation day in the published snapshot (downloads reports.parquet from the data repo)."""
    repo = os.environ.get("DATA_REPO", "mashbat1/gov-crawler")
    tags = subprocess.run(["gh", "release", "list", "-R", repo, "--limit", "100", "--json", "tagName,createdAt", "--jq",
                           '[.[]|select(.tagName|startswith("data-"))]|sort_by(.createdAt)|last|.tagName'],
                          capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["gh", "release", "download", tags, "-R", repo, "-p", "reports.parquet", "-D", ctx.workdir, "--clobber"],
                   check=True)
    t = pq.read_table(os.path.join(ctx.workdir, "reports.parquet"), columns=["created_at"])
    counts = collections.Counter(str(v)[:10] for v in t.column("created_at").to_pylist() if v)
    os.remove(os.path.join(ctx.workdir, "reports.parquet"))
    log(f"snapshot {tags}: {sum(counts.values())} reports on {len(counts)} days")
    return dict(counts)


def slim(r):
    r = dict(r)
    form = r.pop("reportForm", None) or {}
    r["reportFormId"], r["reportFormName"] = form.get("id"), form.get("name")
    body = r.pop("reportFormDataBodies", None)
    r.pop("reportFormOrganizationRecurring", None)
    return r, body


def crawl(ctx):
    st = ctx.state
    if not ctx.stage_done("orgs"):
        rows, total = pages_all(http, ORGS, {"sort": "id,asc"}, size=500)
        ctx.expect("orgs", total)
        if total is not None and len(rows) < total:
            raise RuntimeError(f"orgs: {len(rows)} of {total}")
        for r in rows:
            ctx.write("orgs", flat(r))
        ctx.mark_stage("orgs")
    if not ctx.stage_done("compare"):
        local = snapshot_day_counts(ctx) if not ctx.sample else {}
        days, d, today = [], FIRST_DAY, dt.date.today()
        while d <= today:
            days.append(d.isoformat())
            d += dt.timedelta(days=1)
        st["server"], st["local"] = {}, local

        def count(day):
            nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
            o = http.json(REPORTS, params={"search": f"{FILTER} AND createdAt>{day} AND createdAt<{nxt}", "page": 0, "size": 1})
            st["server"][day] = o.get("totalElements", 0)

        if not ctx.run_items("count", days if not ctx.sample else days[-3:], count, workers=4):
            return False
        st["gap_days"] = sorted(dy for dy, n in st["server"].items() if n > local.get(dy, 0))
        log(f"server {sum(st['server'].values())} reports, snapshot {sum(local.values())}; "
            f"{len(st['gap_days'])} days to re-fetch")
        ctx.mark_stage("compare")
    for dy in st["gap_days"]:
        ctx.expect(f"reports:{dy}", st["server"][dy])

    def fetch_day(day):
        nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
        rows, total = pages_all(http, REPORTS, {"search": f"{FILTER} AND createdAt>{day} AND createdAt<{nxt}", "sort": "id,asc"}, size=100)
        if total is not None and len(rows) < total:
            raise RuntimeError(f"{day}: {len(rows)} of {total}")
        for r in rows:
            head, body = slim(r)
            ctx.write("reports", {**{k: v for k, v in flat(head).items()}, "json": json.dumps(head, ensure_ascii=False)})
            for c in body or []:
                if not c.get("isDelete"):
                    ctx.write("cells", {"report_id": r.get("id"), "line": c.get("line"), "col": c.get("col"), "data": c.get("data")})

    return ctx.run_items("days", st["gap_days"] if not ctx.sample else st["gap_days"][-1:], fetch_day, workers=3)
