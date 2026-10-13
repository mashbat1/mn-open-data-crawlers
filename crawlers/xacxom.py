"""xacxom.iaac.mn — list of public officials who filed asset/income declarations (name, organisation, position,
years). Declaration contents sit behind a per-view reCAPTCHA and are NOT collected."""
from .core import Http, flat, log

API = "https://xacxom.iaac.mn/api"
KEYS = {"organizations": ["type", "id"], "declarants": ["aid_user_num", "organization_id", "position_num"]}
CAP = 2000  # the search API returns at most this many rows
http = Http(rate=2.0)


def crawl(ctx):
    complete = True
    for typ in ("xacxom", "election"):
        stage = f"orgs:{typ}"
        if not ctx.stage_done(stage):
            try:
                orgs = http.json(f"{API}/public/dropdown/organizations", params={"type": typ})["retdata"]
            except Exception as e:  # noqa: BLE001 - "election" may not exist
                log(f"{typ}: organizations not available ({type(e).__name__})")
                orgs = []
            for o in orgs:
                ctx.write("organizations", {**flat(o), "type": typ})
            ctx.state[f"orgs_{typ}"] = [o["id"] for o in orgs]
            ctx.expect(f"organizations:{typ}", len(orgs))
            ctx.mark_stage(stage)
        org_ids = ctx.state[f"orgs_{typ}"]
        if not org_ids:
            continue
        path = "xacxom" if typ == "xacxom" else typ

        def search(org_id, typ=typ, path=path):
            o = http.json(f"{API}/public/{path}/search", method="POST", json={"organizationID": org_id})
            rows = o.get("retdata") or []
            if len(rows) >= CAP:
                ctx.state.setdefault("capped", []).append(f"{typ}:{org_id}")
            for r in rows:
                ctx.write("declarants" if typ == "xacxom" else "candidates", {**flat(r), "query_org_id": org_id})

        complete &= ctx.run_items(f"search:{typ}", org_ids, search, workers=2)
    return complete
