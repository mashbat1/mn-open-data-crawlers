"""Retry the unavailable public registration table, then resume its crawler.

Does not alter TLS, browser identity, login or challenge handling. Status is
written to work_companies/service_retry.json. Polling ends after 24 hours.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time

from crawlers.companies import lifecycle_preflight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--interval-min", type=float, default=30)
    args = parser.parse_args()
    if args.hours <= 0 or args.interval_min < 5:
        parser.error("Use a positive duration and at least 5 minutes between checks")
    work = Path("work_companies")
    work.mkdir(exist_ok=True)
    end = time.monotonic() + args.hours * 3600
    while time.monotonic() < end:
        status = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "complete": False}
        try:
            status["recordsTotal"] = lifecycle_preflight()
            status["service"] = "available"
        except Exception as exc:
            status.update(service="unavailable", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        (work / "service_retry.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(status), flush=True)
        if status["service"] == "available":
            result = subprocess.run([sys.executable, "local.py", "companies", "--budget-min", "120", "--wait-for-lock"])
            if result.returncode == 0:
                return
            # Preserve a genuine crawl failure for review, rather than repeatedly
            # downloading the full registry against a deterministic parsing bug.
            raise SystemExit("Registry service recovered but crawl needs attention; saved data preserved")
        until = min(end, time.monotonic() + args.interval_min * 60)
        while time.monotonic() < until:
            time.sleep(min(60, max(0, until - time.monotonic())))
    print("Retry window ended; service remains unavailable and incomplete", flush=True)


if __name__ == "__main__":
    main()
