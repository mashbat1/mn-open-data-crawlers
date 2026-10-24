"""Recover complete JSON lines from an interrupted gzip stream without inventing its missing tail."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import zlib
import re


def recover(source, target):
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    pending = b""
    counts = Counter()
    with Path(source).open("rb") as inp, gzip.open(target, "wb") as out:
        for compressed in iter(lambda: inp.read(1 << 20), b""):
            lines = (pending + inflater.decompress(compressed)).split(b"\n")
            pending = lines.pop()
            for line in lines:
                row = json.loads(line)
                counts[str(row.get("period", ""))] += 1
                out.write(line + b"\n")
    return {"rows": sum(counts.values()), "period_rows": dict(counts), "discarded_partial_tail_bytes": len(pending),
            "partial_tail_identifiers": {key: match.group(1) for key in ("period", "register")
                                         if (match := re.search(r'"' + key + r'"\s*:\s*"([^"]+)"', pending.decode("utf-8", errors="replace")))},
            "original_stream_complete": inflater.eof,
            "original_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            "recovered_sha256": hashlib.sha256(Path(target).read_bytes()).hexdigest()}
