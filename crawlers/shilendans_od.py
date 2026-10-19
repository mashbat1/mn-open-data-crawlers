"""Shilendans open-data bulk export: every open-data form, every month, all organisations' rows in one request
(the portal's own «нээлттэй өгөгдөл» export). A form-month is done only when its request succeeded."""
import datetime as dt
import json

from .core import Http, log
from .shilendans_common import REPORT, REPORTS, pages_all

OPEN_DATA = REPORTS + "/openData1"
KEYS = {"od_rows": ["form_id", "year", "month", "seq"], "od_forms": ["id"]}
http = Http(rate=0.6, timeout=900, retries=6)


def crawl(ctx):
    st = ctx.state
    if not ctx.stage_done("forms"):
        forms, _ = pages_all(http, REPORT + "/reportForms", {"sort": "id,asc"}, size=500)
        groups, _ = pages_all(http, REPORT + "/reportGroups", {"sort": "id,asc"}, size=500)
        st["groups"] = [g["id"] for g in groups]
        st["cols"], tasks = {}, []
        today = dt.date.today()
        for f in forms:
            if not (f.get("isOpenData") and f.get("isActive")):
                continue
            detail = http.json(f"{REPORT}/reportForms/{f['id']}")
            cols = [h["id"] for h in detail.get("reportFormHeaders") or []]
            years = http.json(REPORTS + "/distinctByYear", params={"reportFormId": f["id"]}) or []
            ctx.write("od_forms", {"id": f["id"], "name": f.get("name"), "columns": json.dumps(detail.get("reportFormHeaders"), ensure_ascii=False)})
            if not cols:
                continue
            st["cols"][str(f["id"])] = cols
            for y in years:
                for m in range(1, 13):
                    if dt.date(int(y), m, 1) <= today:
                        tasks.append(f"{f['id']}:{y}:{m}")
        st["tasks"] = sorted(tasks, key=lambda t: (t.split(":")[0] != "1116", t))
        log(f"{len(st['cols'])} open-data forms, {len(tasks)} form-months")
        ctx.mark_stage("forms")
    st.setdefault("counts", {})

    def one(task):
        fid, y, m = task.split(":")
        body = {"reportFormId": int(fid), "columns": st["cols"][fid], "years": [int(y)], "months": [int(m)],
                "reportGroupIds": st["groups"], "tez": [], "ttz": [], "orgs": []}
        rows = http.json(OPEN_DATA, method="POST", json=body, timeout=900)
        if not isinstance(rows, list):
            raise RuntimeError(f"unexpected answer: {str(rows)[:100]}")
        for seq, r in enumerate(rows):
            ctx.write("od_rows", {"form_id": int(fid), "year": int(y), "month": int(m), "seq": seq,
                                  "org_register": r.get("registerNo"), "org_name": r.get("name"), "line": r.get("line"),
                                  "data": json.dumps({k: v for k, v in r.items() if len(k) <= 2 and k != "id" and v not in (None, "")},
                                                     ensure_ascii=False)})
        st["counts"][task] = len(rows)

    tasks = st["tasks"] if not ctx.sample else [t for t in st["tasks"] if t.startswith("1116:")][:2]
    done = ctx.run_items("od", tasks, one, workers=2)
    ctx.expect("od_rows", sum(st["counts"].values()))
    return done
