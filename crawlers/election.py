"""General Election Commission (Сонгуулийн ерөнхий хороо, СЕХ) election data.

gec.gov.mn now answers 410 Gone. The commission's current site is https://m-election.mn (Next.js front end of
the e-election.mn back office, which is a login-only ERP). m-election.mn renders only page layouts; every list
(results, candidates, party finance reports, donations) is loaded from /api/get-indicator, and its robots.txt
says `Disallow: /api/`. So the site itself cannot be crawled for data, and is not.

The commission publishes its election data as an organisation on the national open-data portal
(opendata.gov.mn, org register 9128719): results per election / constituency / polling station, candidates and
their background, elected members and seats, turnout, constituencies and mandates, polling stations, party and
coalition registrations, election-expense reports (Сонгуулийн зардлын нэгдсэн тайлан), observers, control
counts. This crawler takes every GEC dataset there plus election datasets of other publishers (statistics
office election tables, the Supreme Court's register of political parties), and parses every file into rows.
Party annual finance reports and donation lists exist only behind m-election.mn's disallowed /api/.
"""
import re

from .core import Http, flat, log, strip_tags
from .opendata_gov import BASE, MAX_BYTES, fetch_and_parse, media_url, obey_robots, search_all

GEC_ORG = "9128719"
OTHER = re.compile(r"сонгууль|сонгуулийн|улс төрийн нам", re.I)
QUERIES = ("Сонгууль", "Сонгуулийн", "Улс төрийн нам")
KEYS = {"election_datasets": ["id"], "election_resources": ["file_id"], "election_files": ["file_id"],
        "election_rows": ["file_id", "sheet", "row"]}
http = Http(rate=1.0, timeout=300, retries=4)


def classify(name):
    """Election level, year and by-election flag from a dataset title and description (best effort)."""
    n = (name or "").lower()
    if re.search(r"\bуих\b|их хурл", n):
        level = "parliament"
    elif re.search(r"ерөнхийлөгч", n):
        level = "president"
    elif re.search(r"\bитх\b|иргэдийн төлөөлөгчдийн хур|орон нутгийн хур", n):
        level = "local_assembly"
    else:
        level = None
    years = re.findall(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)", n)
    return {"election_level": level, "election_year": int(years[-1]) if len(years) == 1 else None,
            "election_years": ",".join(years) or None, "by_election": "нөхөн" in n}


def crawl(ctx):
    st = ctx.state
    obey_robots(http, BASE, ["/api/v1/front/search", "/api/v1/media/x"])
    if not ctx.stage_done("catalog"):
        gec, total = search_all(http, ctx.sample, org_reg_num=GEC_ORG)
        ctx.expect("election_datasets:gec", total)
        found = {it["id"]: it for it in gec}
        for q in QUERIES:
            items, _ = search_all(http, ctx.sample, query=q)
            for it in items:
                text = f"{it.get('name')} {it['data'].get('name')}"
                if it["org"]["reg_num"] == GEC_ORG or OTHER.search(text):
                    found[it["id"]] = it
        plan = []
        for it in sorted(found.values(), key=lambda x: x["id"]):
            files = it.get("files") or []
            ctx.write("election_datasets", {
                **flat({k: v for k, v in it.items() if k != "files"}), "publisher_reg_num": it["org"]["reg_num"],
                "publisher": it["org"]["name"], "topic": it["data"]["name"], "series_uuid": it["data"]["uuid"],
                "from_gec": it["org"]["reg_num"] == GEC_ORG, "n_files": len(files),
                **classify(f"{it.get('name')} {strip_tags(it.get('description'))}")})
            for f in files:
                parse = (f.get("size") or 0) < MAX_BYTES
                ctx.write("election_resources", {"file_id": f["id"], "detail_id": it["id"], "url": media_url(f["path"]),
                                                 "parse_status": "parse" if parse else "over_50MB",
                                                 **{k: v for k, v in flat(f).items() if k != "id"}})
                if parse:
                    plan.append({"file_id": f["id"], "detail_id": it["id"], "path": f["path"],
                                 "ext": (f.get("ext") or "").lower(), "size": f.get("size")})
        st["plan"] = plan
        ctx.expect("election_datasets", len(found))
        ctx.expect("election_resources", sum(len(it.get("files") or []) for it in found.values()))
        log(f"{len(found)} election datasets ({sum(1 for it in found.values() if it['org']['reg_num'] == GEC_ORG)}"
            f" from GEC, server total {total}), {len(plan)} files to parse")
        ctx.mark_stage("catalog")
    files = {str(p["file_id"]): p for p in st["plan"]}
    ok = ctx.run_items("files", list(files),
                       lambda k: fetch_and_parse(ctx, http, files[k], "election_rows", "election_files"), workers=2)
    st["coverage_scope"] = {
        "source": "GEC datasets on opendata.gov.mn (org 9128719) + other publishers' election/party datasets",
        "gec_site": "https://m-election.mn (data only via /api/, disallowed by robots.txt; not crawled)",
        "not_covered": "party annual finance reports and donation lists published only on m-election.mn"}
    return ok
