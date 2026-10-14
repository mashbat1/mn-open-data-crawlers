"""parliament.mn — members, votes, bills (public JSON API used by the site itself)."""
from .core import Http, flat, items_of, log

BASE = "https://parliament.mn/api"
DRAFTS = "https://api-d.parliament.mn/tusul/tusulList"
DRAFTS_APP = "56cc90a2-83a0-4e1f-994d-7fdebfdfbf68"      # public app id from the site's bundle
DRAFTS_PARENT = "6101970e-2fb0-4394-8113-db13d7374fb6"
KEYS = {"sessions": ["key"], "members": ["id"], "member_details": ["id"], "polls": ["id"], "votes": ["member_id", "pollId"],
        "meetings": ["id"], "meeting_index": ["id"], "agendas": ["id"], "bills": ["id"], "law_archives": ["id"], "units": ["id"],
        "member_drafts": ["member_id", "id"], "d_drafts": ["id"]}
http = Http(rate=1.5, timeout=90)


def pages(url, params=None, page_size=100, sample=False):
    """DRF-style lists: follows `next`, else increments `page` until a short page."""
    params = dict(params or {})
    params.update(page=1, page_size=page_size)
    while True:
        o = http.json(url, params=params)
        rows = items_of(o)
        yield o, rows
        if sample or not rows:
            return
        nxt = o.get("next") if isinstance(o, dict) else None
        if nxt:
            url, params = nxt, None
            continue
        if isinstance(o, dict) and "next" in o:  # DRF says there is no next page
            return
        if len(rows) < page_size:
            return
        params["page"] += 1


def crawl(ctx):
    complete = True
    if not ctx.stage_done("lists"):
        sessions = items_of(http.json(f"{BASE}/meeting/public/meeting-sessions/"))
        for s in sessions:
            ctx.write("sessions", flat(s))
        ml = http.json(f"{BASE}/parliament_members_list/")
        members = ml["members"]
        ctx.expect("members", len(members))
        for m in members:
            ctx.write("members", flat(m))
        for kind in ("COMMITTEE", "SUBCOMMITTEE", "PARLIAMENT"):
            for _, rows in pages(f"{BASE}/parliament/units/", {"unit_type": kind}, sample=ctx.sample):
                for r in rows:
                    ctx.write("units", flat(r))
        n = 0
        for o, rows in pages(f"{BASE}/meeting/public-polls/", sample=ctx.sample):
            ctx.expect("polls", o.get("count"))
            if n == 0:  # the full meeting index (the meetings endpoint itself only lists the current session)
                for m in (o.get("filter_options") or {}).get("meetings") or []:
                    ctx.write("meeting_index", flat(m))
            for r in rows:
                ctx.write("polls", flat(r))
                n += 1
        for s in sessions:
            for _, rows in pages(f"{BASE}/meeting/public/meetings/", {"session": s["key"]}, sample=ctx.sample):
                for r in rows:
                    ctx.write("meetings", flat({**r, "session": s["key"]}))
        for o, rows in pages(f"{BASE}/meeting/public-agendas/", sample=ctx.sample):
            if isinstance(o, dict) and o.get("count"):
                ctx.expect("agendas", o["count"])
            for r in rows:
                ctx.write("agendas", flat(r))
        bills = items_of(http.json(f"{BASE}/bills-public/"))
        ctx.expect("bills", len(bills))
        for r in bills:
            ctx.write("bills", flat(r))
        for o, rows in pages(f"{BASE}/law-archives/", page_size=200, sample=ctx.sample):
            ctx.expect("law_archives", o.get("count"))
            for r in rows:
                ctx.write("law_archives", flat(r))
        page = 1
        while True:
            o = http.json(DRAFTS, method="POST", json={"appId": DRAFTS_APP, "limit": "50", "page": str(page),
                                                        "parentId": DRAFTS_PARENT, "orderBy": "publishDate", "orderDir": "DESC"})
            rows = o.get("list") or []
            ctx.expect("d_drafts", o.get("count"))
            for r in rows:
                ctx.write("d_drafts", flat(r))
            if ctx.sample or len(rows) < 50:
                break
            page += 1
        ctx.state["member_ids"] = [m["id"] for m in members]
        log(f"lists: {len(members)} members, {n} polls")
        ctx.mark_stage("lists")
    member_ids = ctx.state["member_ids"]

    def detail(mid):
        ctx.write("member_details", flat(http.json(f"{BASE}/parliament_member/detail/{mid}/")))
        for r in items_of(http.json(f"{BASE}/members/{mid}/submitted-drafts/")):
            ctx.write("member_drafts", flat({**r, "member_id": mid}))

    complete &= ctx.run_items("member_details", member_ids, detail, workers=2)

    def votes(mid):
        o = http.json(f"{BASE}/meeting/member/{mid}/voting-history/", timeout=180)
        rows = o.get("data") or []
        if o.get("count") is not None and o["count"] != len(rows):
            raise RuntimeError(f"member {mid}: count {o['count']} but {len(rows)} rows")  # retried next run
        ctx.state["expect"][f"votes:{mid}"] = len(rows)
        for r in rows:
            ctx.write("votes", flat({**r, "member_id": mid}))

    complete &= ctx.run_items("votes", member_ids, votes, workers=2)
    return complete
