"""Moves crawl state and output between the Actions runner and the PRIVATE data repository's releases.
    python ci.py restore <source>    # fetch state.json of the unfinished crawl (if any)
    python ci.py save <source>       # upload new part files + state; when the crawl is complete -> finalize
Work in progress lives in release `wip-<source>`; a finished crawl becomes release `<source>-YYYY-MM-DD` with one
Parquet file per table and manifest.json (row counts vs. what the server reported). Needs GH_TOKEN (fine-grained
token with Contents read/write on DATA_REPO)."""
import glob
import gzip
import hashlib
import importlib
import json
import os
import subprocess
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

REPO = os.environ.get("DATA_REPO", "mashbat1/gov-crawler")
WORK = os.environ.get("WORKDIR", "work")


def gh(*args, check=True, capture=False):
    r = subprocess.run(["gh", *args, "-R", REPO], text=True, capture_output=True)
    if check and r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed: {r.stderr.strip()[:500]}")
    return r.stdout if capture else r.returncode


def release_exists(tag):
    return gh("release", "view", tag, check=False, capture=False) == 0


def upload(tag, files):
    for i in range(0, len(files), 20):
        for attempt in range(4):
            if gh("release", "upload", tag, *files[i:i + 20], "--clobber", check=False) == 0:
                break
            time.sleep(20 * (attempt + 1))
        else:
            raise SystemExit(f"upload to {tag} failed")


def restore(src):
    os.makedirs(WORK, exist_ok=True)
    tag = f"wip-{src}"
    if release_exists(tag):
        gh("release", "download", tag, "-p", "state.json", "-D", WORK, "--clobber")
        print(f"resuming {src} from {tag}")
    else:
        print(f"fresh crawl of {src}")


def column_type(vals):
    kinds = {type(v) for v in vals if v is not None}
    if not kinds:
        return pa.string()
    if kinds <= {bool}:
        return pa.bool_()
    if kinds <= {int}:
        return pa.int64() if all(-2**63 <= v < 2**63 for v in vals if v is not None) else pa.string()
    if kinds <= {int, float}:
        return pa.float64()
    return pa.string()


def to_table(rows):
    cols = {}
    for r in rows:
        for k in r:
            cols.setdefault(k, None)
    arrays = {}
    for c in cols:
        vals = [r.get(c) for r in rows]
        t = column_type(vals)
        if t == pa.string():
            vals = [None if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                    for v in vals]
        arrays[c] = pa.array(vals, type=t)
    return pa.table(arrays)


def write_table(files, key_cols, path, chunk=200_000, keep=None, file_versions=None):
    """Streams part files into one Parquet file without holding the table in memory (millions of rows killed the
    runner). Pass 1 fixes the schema from every row; pass 2 writes newest files first and drops older copies of the
    same key (later runs overwrite earlier ones)."""
    kinds = {}
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if keep is not None and not keep(row):
                    continue
                for k, v in row.items():
                    if v is not None:
                        kinds.setdefault(k, set()).add(type(v).__name__)
    cols = list(kinds)
    types = {c: column_type([{"bool": True, "int": 1, "float": 1.5}.get(t, "x") for t in kinds[c]]) for c in cols}
    schema = pa.schema([(c, types[c]) for c in cols])

    def conv(v, t):
        if v is None:
            return None
        if t == pa.string():
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        return v

    seen, buf, n = set(), [], 0
    writer = pq.ParquetWriter(path, schema, compression="zstd", compression_level=9)

    def flush():
        if buf:
            writer.write_table(pa.table({c: pa.array([conv(r.get(c), types[c]) for r in buf], type=types[c]) for c in cols},
                                        schema=schema))
            buf.clear()
    # Local timestamp run IDs and Actions numeric run IDs cannot be compared
    # lexicographically. Release asset creation times provide their actual order.
    versions = file_versions or {}
    for f in sorted(files, key=lambda p: (versions.get(os.path.basename(p), ""), os.path.basename(p)), reverse=True):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if keep is not None and not keep(r):
                    continue
                k = tuple(str(r.get(c)) for c in key_cols) if key_cols else hashlib.md5(line.encode()).digest()
                if k in seen:
                    continue
                seen.add(k)
                buf.append(r)
                n += 1
                if len(buf) >= chunk:
                    flush()
    flush()
    writer.close()
    return n


def finalize(src):
    tag = f"wip-{src}"
    source = importlib.import_module(f"crawlers.{src}")
    keys = getattr(source, "KEYS", {})
    with open(os.path.join(WORK, "state.json"), encoding="utf-8") as fh:
        state = json.load(fh)
    if not state.get("complete") or any(state.get("errors", {}).values()):
        raise SystemExit("crawl is incomplete; preserving the work-in-progress release")
    validator = getattr(source, "validate_snapshot", None)
    if validator:
        validator(state, WORK)
    dl = os.path.join(WORK, "all_parts")
    os.makedirs(dl, exist_ok=True)
    gh("release", "download", tag, "-p", "*.jsonl.gz", "-D", dl, "--clobber")
    assets = json.loads(gh("release", "view", tag, "--json", "assets", capture=True))["assets"]
    file_versions = {asset["name"]: asset["createdAt"] for asset in assets}
    by_table = {}
    for f in sorted(glob.glob(os.path.join(dl, "*.jsonl.gz"))):
        by_table.setdefault(os.path.basename(f).split(".")[0], []).append(f)
    out = os.path.join(WORK, "final")
    os.makedirs(out, exist_ok=True)
    date = time.strftime("%Y-%m-%d")
    manifest = {"source": src, "crawled": date, "runs": state.get("runs"), "tables": {},
                "coverage_scope": state.get("coverage_scope"),
                "server_reported": state.get("expect", {}),
                "failed_items": {k: len(v) for k, v in state.get("errors", {}).items() if v},
                "failed_examples": {k: dict(list(v.items())[:5]) for k, v in state.get("errors", {}).items() if v}}
    for table, files in by_table.items():
        path = os.path.join(out, f"{src}__{table}.parquet")
        n = write_table(files, keys.get(table), path, keep=getattr(source, "ROW_FILTERS", {}).get(table),
                        file_versions=file_versions)
        manifest["tables"][table] = {"rows": n, "bytes": os.path.getsize(path)}
        print(f"{table}: {n} rows, {os.path.getsize(path) / 1e6:.1f} MB")
        expected = state.get("expect", {}).get(table)
        if isinstance(expected, int) and n != expected:
            raise SystemExit(f"{table}: {n} unique rows vs {expected} expected; work-in-progress preserved")
    json.dump(manifest, open(os.path.join(out, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    final_tag = f"{src}-{date}"
    if release_exists(final_tag):
        final_tag += time.strftime("-%H%M")
    notes = (f"{src} crawl finished {date} after {state.get('runs')} run(s).\n\n" +
             "\n".join(f"- {t}: {v['rows']:,} rows" for t, v in manifest["tables"].items()) +
             "\n\nmanifest.json lists the totals each server reported and any items that failed.")
    gh("release", "create", final_tag, "--title", f"{src} {date}", "--notes", notes, "--latest=false")
    upload(final_tag, sorted(glob.glob(os.path.join(out, "*"))))
    remote = json.loads(gh("release", "view", final_tag, "--json", "assets", capture=True))["assets"]
    sizes = {a["name"]: a["size"] for a in remote}
    for f in glob.glob(os.path.join(out, "*")):
        if sizes.get(os.path.basename(f)) != os.path.getsize(f):
            raise SystemExit(f"verification failed for {f}")
    with open(os.path.join(WORK, "published.json"), "w", encoding="utf-8") as fh:
        json.dump({"source": src, "tag": final_tag, "verified_sizes": sizes}, fh)
    gh("release", "delete", tag, "--yes", "--cleanup-tag")
    print(f"published {final_tag}; removed {tag}")


def save(src):
    tag = f"wip-{src}"
    if not release_exists(tag):
        gh("release", "create", tag, "--title", f"{src} (crawl in progress)", "--notes",
           "Partial output of an unfinished crawl. Replaced by a dated release when complete.",
           "--prerelease", "--latest=false")
    result_path = os.path.join(WORK, "result.json")
    result = json.load(open(result_path))
    validation_error = None
    validator = getattr(importlib.import_module(f"crawlers.{src}"), "validate_snapshot", None)
    if result.get("complete") and validator:
        state_path = os.path.join(WORK, "state.json")
        state = json.load(open(state_path, encoding="utf-8"))
        try:
            validator(state, WORK)
        except Exception as exc:
            validation_error = str(exc)
            state["complete"] = False
            state["resume_status"] = "needs_attention"
            state.setdefault("errors", {}).setdefault("snapshot_validation", {})["source"] = validation_error
            result["complete"] = False
            json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False)
            json.dump(result, open(result_path, "w"))
    parts = sorted(glob.glob(os.path.join(WORK, "parts", "*.jsonl.gz")))
    upload(tag, parts + [os.path.join(WORK, "state.json")])
    print(f"saved {len(parts)} part files to {tag}")
    if validation_error:
        raise SystemExit("source validation failed; uploaded partial data preserved: " + validation_error)
    if result.get("complete"):
        finalize(src)


if __name__ == "__main__":
    {"restore": restore, "save": save, "finalize": finalize}[sys.argv[1]](sys.argv[2])
