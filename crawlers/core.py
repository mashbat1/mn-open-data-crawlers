"""Shared machinery: a polite rate-limited HTTP client, resumable state, row sinks and a time budget.

A crawl runs inside a GitHub Actions job (max 6 h). When the budget runs out the crawler stops cleanly, the
state is saved and the next job continues where this one stopped. Every item a crawler processes is keyed, so
finished items are skipped on resume and failed ones are retried (up to MAX_ATTEMPTS runs).
"""
import gzip
import json
import os
import random
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from html.parser import HTMLParser

if os.name == "nt":
    # Windows can build the site's incomplete certificate chain using its native
    # trust store. Keep verification enabled (audit.mn omits an intermediate).
    import truststore
    truststore.inject_into_ssl()

import requests

USER_AGENT = ("mn-open-data-crawlers/1.0 (public-interest transparency research; "
              "+https://github.com/mashbat1/mn-open-data-crawlers)")
MAX_ATTEMPTS = 3


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class NotFound(Exception):
    pass


class TimeBudgetExpired(Exception):
    """A partially processed item pauses without consuming its network-error retry budget."""
    pass


class Http:
    """Thread-safe client: at most `rate` request starts per second across all threads, retries with backoff
    on 5xx / 429 / network errors, honours Retry-After."""

    def __init__(self, rate=1.0, timeout=60, retries=6, headers=None, verify=True):
        self.interval = 1.0 / rate
        self.timeout, self.retries, self.verify = timeout, retries, verify
        self.headers = {"User-Agent": USER_AGENT, "Accept-Language": "mn,en;q=0.8", **(headers or {})}
        self._lock = threading.Lock()
        self._next = 0.0
        self._local = threading.local()
        self.count = 0

    def _session(self):
        s = getattr(self._local, "s", None)
        if s is None:
            s = self._local.s = requests.Session()
            s.headers.update(self.headers)
        return s

    def _wait_turn(self):
        with self._lock:
            now = time.monotonic()
            t = max(now, self._next)
            self._next = t + self.interval
        if t > now:
            time.sleep(t - now)

    def request(self, method, url, **kw):
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("verify", self.verify)
        last = None
        for attempt in range(self.retries + 1):
            self._wait_turn()
            try:
                r = self._session().request(method, url, **kw)
                self.count += 1
                if r.status_code == 404:
                    raise NotFound(url)
                if r.status_code in (429, 500, 502, 503, 504, 520, 521, 522, 524):
                    last = f"HTTP {r.status_code}"
                    wait_s = float(r.headers.get("Retry-After") or 0) or min(300, 3 * 2 ** attempt)
                    time.sleep(wait_s + random.random())
                    continue
                r.raise_for_status()
                return r
            except NotFound:
                raise
            except (requests.ConnectionError, requests.Timeout) as e:
                last = type(e).__name__
                time.sleep(min(300, 3 * 2 ** attempt) + random.random())
        raise RuntimeError(f"giving up after {self.retries + 1} tries ({last}): {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def json(self, url, method="GET", **kw):
        return self.request(method, url, **kw).json()


class Sink:
    """Appends rows (dicts) to gzip JSON-lines part files, rotating every `rotate` rows."""

    def __init__(self, outdir, table, run_id, sample=False, rotate=250_000):
        self.outdir, self.table, self.run_id, self.sample, self.rotate = outdir, table, run_id, sample, rotate
        self.lock = threading.Lock()
        self.f, self.n_file, self.seq, self.n = None, 0, 0, 0
        self.keys = set()

    def _open(self):
        self.seq += 1
        path = os.path.join(self.outdir, f"{self.table}.{self.run_id}.{self.seq:03d}.jsonl.gz")
        self.f = gzip.open(path, "wt", encoding="utf-8")
        self.n_file = 0

    def write(self, row):
        with self.lock:
            self.n += 1
            if self.sample:
                self.keys.update(row.keys())
                return
            if self.f is None or self.n_file >= self.rotate:
                self.close()
                self._open()
            self.f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self.n_file += 1

    def close(self):
        if self.f:
            self.f.close()
            self.f = None


class Ctx:
    def __init__(self, source, workdir, sample=False, budget_min=330, run_id=None):
        self.source, self.workdir, self.sample = source, workdir, sample
        self.deadline = time.monotonic() + budget_min * 60
        self.run_id = run_id or os.environ.get("GITHUB_RUN_ID") or time.strftime("%Y%m%d%H%M%S")
        self.parts = os.path.join(workdir, "parts")
        os.makedirs(self.parts, exist_ok=True)
        self.state_path = os.path.join(workdir, "state.json")
        self.state = json.load(open(self.state_path, encoding="utf-8")) if os.path.exists(self.state_path) else {}
        self.state.setdefault("done", {})       # stage -> [keys]
        self.state.setdefault("attempts", {})   # stage -> {key: n}
        self.state.setdefault("errors", {})     # stage -> {key: last error}
        self.state.setdefault("expect", {})     # table/label -> number the server reports
        self.state.setdefault("stages_complete", [])
        self.state["runs"] = self.state.get("runs", 0) + 1
        self._done_sets = {k: set(v) for k, v in self.state["done"].items()}
        self.sinks = {}
        self._save_lock = threading.Lock()
        self._last_save = time.monotonic()

    # ---- time / state ----
    def time_up(self):
        return time.monotonic() > self.deadline

    def save(self):
        with self._save_lock:
            for _ in range(20):  # worker threads may be adding to nested dicts while we serialise
                try:
                    self.state["done"] = {k: sorted(v) for k, v in list(self._done_sets.items())}
                    data = json.dumps(self.state, ensure_ascii=False)
                    break
                except RuntimeError:
                    time.sleep(0.05)
            else:
                return
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.state_path)
            self._last_save = time.monotonic()

    def expect(self, label, n):
        self.state["expect"][label] = n

    def stage_done(self, stage):
        return stage in self.state["stages_complete"]

    def mark_stage(self, stage):
        if stage not in self.state["stages_complete"]:
            self.state["stages_complete"].append(stage)
        self.save()

    def done_set(self, stage):
        return self._done_sets.setdefault(stage, set())

    def limit(self, items, n=3):
        items = list(items)
        return items[:n] if self.sample else items

    # ---- output ----
    def sink(self, table):
        if table not in self.sinks:
            self.sinks[table] = Sink(self.parts, table, self.run_id, self.sample)
        return self.sinks[table]

    def write(self, table, row):
        self.sink(table).write(row)

    def close(self):
        for s in self.sinks.values():
            s.close()
        self.save()

    # ---- the work loop ----
    def run_items(self, stage, items, fn, key=str, workers=2):
        """Runs fn(item) for unfinished items, retrying at most MAX_ATTEMPTS times.
        Returns True only when every requested item succeeded; exhausted failures remain incomplete."""
        done = self.done_set(stage)
        attempts = self.state["attempts"].setdefault(stage, {})
        errors = self.state["errors"].setdefault(stage, {})
        items = list(items)
        todo = [it for it in items if str(key(it)) not in done and attempts.get(str(key(it)), 0) < MAX_ATTEMPTS]
        if self.sample:
            todo = todo[:3]
        log(f"{stage}: {len(todo)} to do ({len(done)} done before)")
        ok = fail = 0
        it = iter(todo)
        futs = {}
        with ThreadPoolExecutor(workers) as ex:
            while True:
                while len(futs) < workers * 2 and not self.time_up():
                    x = next(it, None)
                    if x is None:
                        break
                    futs[ex.submit(fn, x)] = str(key(x))
                if not futs:
                    break
                finished, _ = wait(list(futs), return_when=FIRST_COMPLETED)
                for f in finished:
                    k = futs.pop(f)
                    try:
                        f.result()
                        done.add(k)
                        errors.pop(k, None)
                        ok += 1
                    except TimeBudgetExpired:
                        pass
                    except Exception as e:  # noqa: BLE001 - recorded and retried on the next run
                        attempts[k] = attempts.get(k, 0) + 1
                        errors[k] = f"{type(e).__name__}: {str(e)[:200]}"
                        fail += 1
                    if (ok + fail) % 200 == 0:
                        log(f"{stage}: {ok} ok, {fail} failed, {len(done)} total done")
                if time.monotonic() - self._last_save > 120:
                    self.save()
        remaining = [x for x in todo if str(key(x)) not in done and attempts.get(str(key(x)), 0) < MAX_ATTEMPTS]
        log(f"{stage}: finished this run with {ok} ok, {fail} failed, {len(remaining)} remaining")
        self.save()
        return all(str(key(x)) in done for x in items)


# ---- small helpers ----
def items_of(o):
    """The list inside a typical JSON list response."""
    if isinstance(o, list):
        return o
    for k in ("results", "data", "items", "list", "retdata", "rows", "features"):
        if isinstance(o, dict) and isinstance(o.get(k), list):
            return o[k]
    return []


def flat(d, prefix=""):
    """Top-level scalars stay columns; nested values become JSON strings."""
    out = {}
    for k, v in d.items():
        out[prefix + k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    return out


NL = chr(10)


class _Text(HTMLParser):
    """Visible text of a page (or of the first element with `root_class`), skipping scripts, navigation and any
    element with one of `drop_classes`. Depth is counted per tag name, so unclosed tags elsewhere don't matter."""
    SKIP = {"script", "style", "noscript", "head", "svg", "nav", "footer", "header", "form", "select", "button", "template"}
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article", "td"}
    VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base", "col", "embed", "source", "wbr"}

    def __init__(self, root_class=None, drop_classes=()):
        super().__init__(convert_charrefs=True)
        self.root_class, self.drop = root_class, set(drop_classes)
        self.in_root = root_class is None
        self.root_done = False
        self.root_tag, self.root_depth = None, 0
        self.skip_tag, self.skip_depth = None, 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append(NL)
        if tag in self.VOID:
            return
        classes = set((dict(attrs).get("class") or "").split())
        if self.root_class and not self.in_root and not self.root_done and self.root_class in classes:
            self.in_root, self.root_tag, self.root_depth = True, tag, 0
        if self.root_tag == tag and self.in_root:
            self.root_depth += 1
        if self.skip_tag is None and (tag in self.SKIP or classes & self.drop):
            self.skip_tag, self.skip_depth = tag, 0
        if self.skip_tag == tag:
            self.skip_depth += 1
        if tag in self.BLOCK:
            self.parts.append(NL)

    def handle_endtag(self, tag):
        if self.skip_tag == tag:
            self.skip_depth -= 1
            if self.skip_depth <= 0:
                self.skip_tag = None
        if self.root_tag == tag and self.in_root:
            self.root_depth -= 1
            if self.root_depth <= 0:
                self.in_root, self.root_done, self.root_tag = False, True, None
        if tag in self.BLOCK:
            self.parts.append(NL)

    def handle_data(self, data):
        if self.in_root and self.skip_tag is None:
            self.parts.append(data)


def html_text(html, root_class=None, drop_classes=()):
    p = _Text(root_class, drop_classes)
    p.feed(html)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()
