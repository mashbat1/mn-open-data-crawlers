"""Run one source crawler for at most --budget-min minutes; resumes from work/state.json.
    python run.py parliament --sample        # a few requests per stage; prints counts and column names only
"""
import argparse
import importlib
import json
import os
import sys

from crawlers.core import Ctx, TimeBudgetExpired, log, should_continue

SOURCES = ["parliament", "xacxom", "legalinfo", "shuukh", "companies", "registry", "sanctions", "egazar", "iaac",
           "shilendans_eza", "shilendans_od", "shilendans_reports",
           "audit", "eiti", "cabinet", "ebarimt", "tender", "tender_archive", "opendatalab",
           "supremecourt", "conscourt", "ub_khural", "csc", "mof", "opendata_gov", "frc", "cmcs", "election"]
MAX_RUNS = 100  # Full registry lookups can require about ten five-hour jobs.

ap = argparse.ArgumentParser()
ap.add_argument("source", choices=SOURCES)
ap.add_argument("--sample", action="store_true")
ap.add_argument("--budget-min", type=float, default=320)
ap.add_argument("--workdir", default="work")
a = ap.parse_args()

mod = importlib.import_module(f"crawlers.{a.source}")
ctx = Ctx(a.source, a.workdir, sample=a.sample, budget_min=a.budget_min)
continuation_path = os.path.join(a.workdir, "CONTINUE")
if os.path.exists(continuation_path):
    os.remove(continuation_path)
ctx.state["complete"] = False
if getattr(mod, 'http', None):
    mod.http.deadline = ctx.deadline
if a.sample and getattr(mod, "http", None):
    mod.http.timeout = min(mod.http.timeout, 30)
    mod.http.retries = min(mod.http.retries, 2)
complete, crashed = False, None
try:
    complete = bool(mod.crawl(ctx))
except TimeBudgetExpired:
    log('crawl time budget reached; state will resume')
except Exception as e:  # noqa: BLE001 - state is saved; the next run resumes
    crashed = f"{type(e).__name__}: {str(e)[:300]}"
    log("crawl stopped:", crashed)
finally:
    ctx.close()

ctx.state["complete"] = complete and not crashed
ctx.save()
rows = {t: s.n for t, s in ctx.sinks.items()}
errors = {k: len(v) for k, v in ctx.state["errors"].items() if v}
log(f"rows this run: {rows}")
log(f"requests: {getattr(mod, 'http', None) and mod.http.count}; failed items: {errors or 'none'}; complete: {ctx.state['complete']}")
if a.sample:
    for t, s in ctx.sinks.items():
        log(f"  {t}: {s.n} rows, columns: {sorted(s.keys)}")
    for k, v in ctx.state["expect"].items():
        log(f"  expect {k} = {v}")
result = {"complete": ctx.state["complete"], "crashed": crashed, "rows": rows, "runs": ctx.state["runs"]}
json.dump(result, open(os.path.join(a.workdir, "result.json"), "w"))
# a crash that repeats the previous run's crash is deterministic: stop instead of looping
repeat = crashed and crashed == ctx.state.get("last_crash")
ctx.state["last_crash"] = crashed
ctx.save()
resume = should_continue(ctx.state, ctx.time_up(), crashed, repeat, MAX_RUNS)
ctx.state["resume_status"] = "complete" if ctx.state["complete"] else ("retryable" if resume else "needs_attention")
ctx.save()
if not a.sample and resume:
    open(continuation_path, "w").close()
sys.exit(1 if (a.sample and crashed) else 0)
