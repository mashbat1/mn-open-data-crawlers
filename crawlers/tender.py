"""Tender's documented Open Data API: plans, invitations, bidders and evaluation committees.

TENDER_API_TOKEN must be supplied by the operator; no website challenge is bypassed.
TENDER_API_BASE may point to a replacement official HTTPS API. Attachments are only
referenced in the returned metadata, never downloaded. Coverage is the records the
issued API credential can access, not an assertion of nationwide completeness.
Documentation: https://opendata.tender.gov.mn/
"""
import hashlib
import json
import math
import os
from urllib.parse import urlparse

from .core import Http, log

KEYS = {"invitations": ["invitationId"], "plans": ["tenderId"],
        "suppliers": ["invitation_id", "row_key"], "committee": ["invitation_id", "row_key"],
        "pages": ["url"], "invitation_links": ["url"], "attachments": ["page_url", "url"],
        "api_responses": ["url", "sha256"]}
http = Http(rate=0.5, timeout=60, retries=3)


def api_base():
    base = os.environ.get("TENDER_API_BASE", "https://opendata.tender.gov.mn/api").rstrip("/")
    parsed = urlparse(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or not (parsed.hostname == "tender.gov.mn" or parsed.hostname.endswith(".tender.gov.mn"))
            or parsed.query or parsed.fragment):
        raise RuntimeError("TENDER_API_BASE must be an official tender.gov.mn HTTPS URL")
    return base


def read_page(path, page, per_page=100):
    token = os.environ.get("TENDER_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Tender API access is not configured: set the operator-issued TENDER_API_TOKEN")
    response = http.json(f"{api_base()}/{path}", method="POST",
                         headers={"Authorization": f"Bearer {token}"},
                         data={"current_page": page, "per_page": per_page})
    if not isinstance(response, dict) or str(response.get("return_code", response.get("returncode"))) != "200":
        raise RuntimeError("Tender API rejected the request (no successful return_code)")
    rows = response.get("data")
    total = response.get("total")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or total is None:
        raise RuntimeError("Tender API returned an invalid paginated response")
    total = int(total)
    if total < 0 or (not rows and (page - 1) * per_page < total):
        raise RuntimeError("Tender API returned an empty page before the reported total")
    if response.get("current_page") is not None and int(response["current_page"]) != page:
        raise RuntimeError("Tender API did not return the requested page")
    return total, rows


def crawl(ctx):
    st = ctx.state
    api_base()
    if not os.environ.get("TENDER_API_TOKEN", "").strip():
        raise RuntimeError("Tender requires operator-issued API access; TENDER_API_TOKEN is missing")
    st["coverage_scope"] = {
        "method": "operator-issued API", "credential_scope_may_be_organisation_only": True,
        "configured_sections": ["plans", "invitations", "suppliers", "committee"],
        "missing_sections": ["contracts", "implementation"],
        "nationwide_completeness_verified": False,
    }
    st.setdefault("tender_ids", {})
    st.setdefault("tender_counts", {})

    def listing(table, path, id_field):
        if ctx.stage_done(f"tender_{table}"):
            return True
        total, first = read_page(path, 1)
        ctx.expect(f"tender:{table}", total)
        seen = st["tender_ids"].setdefault(table, [])
        known = set(seen)

        def page(number):
            count, rows = (total, first) if number == 1 else read_page(path, number)
            if count != total:
                raise RuntimeError("Tender total changed during pagination; resume to reconcile")
            for row in rows:
                identifier = row.get(id_field)
                if identifier in (None, ""):
                    raise RuntimeError(f"Tender {table} row lacks {id_field}")
                ctx.write(table, row)
                identifier = str(identifier)
                if identifier not in known:
                    known.add(identifier)
                    seen.append(identifier)

        pages = list(range(1, max(1, math.ceil(total / 100)) + 1))
        complete = ctx.run_items(f"tender_{table}_pages", pages if not ctx.sample else pages[:1], page, workers=1)
        if complete and not ctx.sample:
            if len(known) != total:
                # Re-read pages on resume rather than accepting duplicate/missing records.
                ctx.done_set(f"tender_{table}_pages").clear()
                ctx.save()
                raise RuntimeError(f"Tender {table}: {len(known)} unique IDs vs {total} reported")
            ctx.mark_stage(f"tender_{table}")
        return complete

    complete = listing("invitations", "invitation", "invitationId")
    complete &= listing("plans", "plan", "tenderId")
    if not complete or ctx.time_up():
        return False

    def details(key):
        invitation_id, table = key.split(":")
        page, seen, expected = 1, set(), None
        while True:
            total, rows = read_page(f"invitation/{invitation_id}/{table}", page)
            if expected is not None and total != expected:
                raise RuntimeError("Tender detail total changed during pagination")
            expected = total
            for row in rows:
                payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
                row_key = hashlib.sha256(payload.encode()).hexdigest()
                seen.add(row_key)
                ctx.write(table, {"invitation_id": invitation_id, "row_key": row_key, "data": payload})
            if page * 100 >= total or ctx.sample:
                break
            if ctx.time_up():
                raise RuntimeError("Tender detail paused at the time budget; retry on resume")
            page += 1
        if not ctx.sample and len(seen) != expected:
            raise RuntimeError("Tender detail unique rows do not match its reported total")
        st["tender_counts"][key] = len(seen)

    keys = [f"{identifier}:{table}" for identifier in st["tender_ids"].get("invitations", [])
            for table in ("suppliers", "committee")]
    log(f"Tender: {len(keys)} invitation detail lists")
    complete &= ctx.run_items("tender_details", keys if not ctx.sample else keys[:2], details, workers=1)
    return complete
