"""Import browser_export.js's public-data JSONL, optionally publish through the existing Releases pipeline.

    python import_tender_export.py path/to/tender-public-export.jsonl --publish

Browser exports stay in wip-tender until a separate full-coverage reconciliation.
Observed linked pages alone cannot establish completeness of every Tender section.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse

from crawlers.core import Ctx

SECTIONS = {"plan", "invitation", "result", "contract", "implementation", "performance",
            "complaint", "blacklist", "supplier", "organization", "customer", "statistics"}


def page_section(url):
    parts = [p for p in urlparse(url).path.split("/") if p]
    if parts and parts[0] in {"mn", "en"}:
        parts.pop(0)
    if not parts or parts[0] not in SECTIONS or any(
            p.lower() in {"create", "edit", "delete", "submit", "login", "account", "profile", "register"} for p in parts[1:]):
        raise ValueError("unexpected procurement page path")
    return parts[0], len(parts) > 1 and parts[0] != "statistics"


def public_url(value):
    u = urlparse(value)
    if (u.scheme != "https" or u.hostname != "www.tender.gov.mn" or u.username or u.password
            or re.search(r"token|secret|auth|session|__cf", u.query, re.I)):
        raise ValueError("export contains a non-public or unexpected URL")
    return value


def import_export(path, work, allow_partial=False):
    ctx = Ctx("tender", str(work))
    manifest = None
    pages = 0
    record_count = 0
    source_ok = False
    observed_sections = {}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                if manifest is not None:
                    raise ValueError("manifest must be the last record")
                typ = record.get("type")
                if typ == "metadata":
                    if source_ok or record_count != 0:
                        raise ValueError("metadata must occur once, at the start")
                    if public_url(record.get("source", "")) != "https://www.tender.gov.mn":
                        raise ValueError("unexpected export source")
                    source_ok = True
                elif typ == "page":
                    if not source_ok:
                        raise ValueError("missing export metadata")
                    url = public_url(record["url"])
                    kind = record.get("kind")
                    section, detail = page_section(url)
                    if kind != section + ("_detail" if detail else "_list"):
                        raise ValueError("unexpected page type or path")
                    observed_sections[section] = observed_sections.get(section, 0) + 1
                    ctx.write("pages", {"url": url, "kind": kind, "title": record.get("title"),
                                       "text": record.get("text"), "tables": json.dumps(record.get("tables", []), ensure_ascii=False),
                                       "links": json.dumps(record.get("links", []), ensure_ascii=False)})
                    pages += 1
                    for link in record.get("links", []):
                        target = public_url(link["url"])
                        if link.get("attachment"):
                            ctx.write("attachments", {"page_url": url, "url": target, "name": link.get("name")})
                        else:
                            match = re.search(r"/invitation/(?:detail/)?([\w-]*\d[\w-]*)/?$", urlparse(target).path)
                            if match:
                                ctx.write("invitation_links", {"url": target, "id": match.group(1), "name": link.get("name")})
                elif typ == "api_response":
                    if not source_ok:
                        raise ValueError("missing export metadata")
                    url = public_url(record["url"])
                    if not re.match(r"^/api/(statistics|invitations?|results?|contracts?|plans?|tenders?)(/|$)", urlparse(url).path):
                        raise ValueError("unexpected public API path")
                    if re.search(r"/(account|profile|auth|login|create|edit|delete|submit)(/|$)", urlparse(url).path, re.I):
                        raise ValueError("unexpected private API path")
                    payload = json.dumps(record.get("data"), ensure_ascii=False, sort_keys=True)
                    ctx.write("api_responses", {"url": url, "sha256": hashlib.sha256(payload.encode()).hexdigest(), "data": payload})
                elif typ == "manifest":
                    if not source_ok or record.get("records") != record_count:
                        raise ValueError("manifest record count does not match the export")
                    manifest = record
                else:
                    raise ValueError("unexpected export record type")
                record_count += 1
        if manifest is None and allow_partial and source_ok and pages > 0:
            manifest = {"complete": False, "stopped": True, "errors": ["Recovered interrupted export without final manifest"]}
        if not source_ok or pages == 0 or manifest is None:
            raise ValueError("export must include metadata, public pages and a final manifest")
        coverage = manifest.get("coverage", {})
        verified = bool(coverage) and all(
            c.get("verified") is True and isinstance(c.get("reported_total"), int)
            and c.get("reported_total") == c.get("unique_invitations") for c in coverage.values())
        ctx.state["complete"] = False
        ctx.state["coverage_scope"] = {"method": "normal browser export", "filters": coverage,
                                       "observed_sections": observed_sections,
                                       "invitation_filter_counts_match": verified,
                                       "missing_sections": sorted({"plan", "invitation", "result", "contract", "implementation"} - observed_sections.keys()),
                                       "nationwide_completeness_verified": False}
        ctx.state["browser_export_errors"] = manifest.get("errors", [])
        ctx.expect("exported_pages", pages)
        with open(path, "rb") as source:
            ctx.state["export_sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
        ctx.save()
        result = {"complete": ctx.state["complete"], "crashed": None, "rows": {t: s.n for t, s in ctx.sinks.items()}}
        Path(work, "result.json").write_text(json.dumps(result), encoding="utf-8")
        return result
    finally:
        ctx.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--workdir", type=Path, default=Path("work_tender_browser"))
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--allow-partial", action="store_true", help="recover a checkpoint file interrupted before its final manifest")
    args = ap.parse_args()
    work = args.workdir.resolve()
    if work.exists():
        raise SystemExit("use a new workdir for each import; existing data is preserved")
    result = import_export(args.file, work, allow_partial=args.allow_partial)
    print(json.dumps(result))
    if args.publish:
        env = dict(os.environ, WORKDIR=str(work), DATA_REPO="mashbat1/gov-crawler", PYTHONIOENCODING="utf-8")
        subprocess.run([sys.executable, str(Path(__file__).with_name("ci.py")), "save", "tender"], env=env, check=True)


if __name__ == "__main__":
    main()
