"""Import browser_export.js's public-data JSONL, optionally publish through the existing Releases pipeline.

    python import_tender_export.py path/to/tender-public-export.jsonl --publish

Only the manifest's verified filter scope can be called complete. Unknown or
partial coverage stays in wip-tender; it never becomes a finished data release.
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


def public_url(value):
    u = urlparse(value)
    if (u.scheme != "https" or u.hostname != "www.tender.gov.mn" or u.username or u.password
            or re.search(r"token|secret|auth|session|__cf", u.query, re.I)):
        raise ValueError("export contains a non-public or unexpected URL")
    return value


def import_export(path, work):
    ctx = Ctx("tender", str(work))
    manifest = None
    pages = 0
    record_count = 0
    source_ok = False
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
                    if kind not in {"invitation_list", "invitation_detail"} or "/invitation" not in urlparse(url).path:
                        raise ValueError("unexpected page type or path")
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
                elif typ == "manifest":
                    if not source_ok or record.get("records") != record_count:
                        raise ValueError("manifest record count does not match the export")
                    manifest = record
                else:
                    raise ValueError("unexpected export record type")
                record_count += 1
        if not source_ok or pages == 0 or manifest is None:
            raise ValueError("export must include metadata, public pages and a final manifest")
        coverage = manifest.get("coverage", {})
        verified = bool(coverage) and all(
            c.get("verified") is True and isinstance(c.get("reported_total"), int)
            and c.get("reported_total") == c.get("unique_invitations") for c in coverage.values())
        ctx.state["complete"] = manifest.get("complete") is True and verified and not manifest.get("errors") and not manifest.get("stopped")
        ctx.state["coverage_scope"] = {"method": "normal browser export", "filters": coverage,
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
    args = ap.parse_args()
    work = args.workdir.resolve()
    if work.exists():
        raise SystemExit("use a new workdir for each import; existing data is preserved")
    result = import_export(args.file, work)
    print(json.dumps(result))
    if args.publish:
        env = dict(os.environ, WORKDIR=str(work), DATA_REPO="mashbat1/gov-crawler", PYTHONIOENCODING="utf-8")
        subprocess.run([sys.executable, str(Path(__file__).with_name("ci.py")), "save", "tender"], env=env, check=True)


if __name__ == "__main__":
    main()
