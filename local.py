"""Run a Mongolia-only crawl, checkpoint to private Releases, clean up only after a verified final publication.

    python local.py audit
    python local.py cabinet --budget-min 30 --max-runs 48
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", choices=["audit", "eiti", "cabinet", "ebarimt", "tender", "shilendans_eza"])
    ap.add_argument("--budget-min", type=float, default=30)
    ap.add_argument("--max-runs", type=int, default=48)
    ap.add_argument("--keep-local", action="store_true")
    ap.add_argument("--wait-for-lock", action="store_true", help="wait for an existing local runner to checkpoint and exit")
    a = ap.parse_args()
    root = Path(__file__).resolve().parent
    os.chdir(root)
    work = root / f"work_{a.source}"
    # An OS-held lock is released even if the runner is interrupted.
    lock = open(root / f".local-{a.source}.lock", "a+b")
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    lock_deadline = time.monotonic() + 7200
    while True:
        try:
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if not a.wait_for_lock or time.monotonic() >= lock_deadline:
                raise SystemExit(f"another local {a.source} runner holds the lock")
            time.sleep(5)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               DATA_REPO="mashbat1/gov-crawler", WORKDIR=str(work))
    subprocess.run(["gh", "repo", "view", env["DATA_REPO"], "--json", "nameWithOwner"], env=env, check=True)
    for attempt in range(a.max_runs):
        print(f"local {a.source}: run {attempt + 1}/{a.max_runs}", flush=True)
        subprocess.run([sys.executable, "run.py", a.source, "--budget-min", str(a.budget_min),
                        "--workdir", str(work)], env=env, check=True)
        result = json.loads((work / "result.json").read_text(encoding="utf-8"))
        state = json.loads((work / "state.json").read_text(encoding="utf-8"))
        subprocess.run([sys.executable, "ci.py", "save", a.source], env=env, check=True)
        if result.get("complete") and not any(state.get("errors", {}).values()):
            receipt = json.loads((work / "published.json").read_text(encoding="utf-8"))
            if receipt.get("source") != a.source or not receipt.get("verified_sizes"):
                raise SystemExit("publication receipt is invalid; local data preserved")
            print(f"published and verified {receipt['tag']}", flush=True)
            if not a.keep_local:
                target = work.resolve()
                if target.parent != root or target.name != f"work_{a.source}" or work.is_symlink():
                    raise SystemExit("unexpected cleanup path; local data preserved")
                shutil.rmtree(target)
                print(f"removed verified local copy {target}", flush=True)
            subprocess.run(['gh', 'workflow', 'run', 'coverage.yml', '-R', 'mashbat1/mn-open-data-crawlers'],
                           env=env, check=False)
            return
        if result.get("crashed") or (not any(state.get("pending_retryable", {}).values()) and any(
                state.get("attempts", {}).get(stage, {}).get(key, 0) >= 3
                for stage, errors in state.get("errors", {}).items() for key in errors)):
            raise SystemExit("crawl needs attention; partial release and local data preserved")
    raise SystemExit("run limit reached; partial release and local data preserved")


if __name__ == "__main__":
    main()
