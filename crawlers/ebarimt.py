"""ebarimt.mn public taxpayer lookup (the one point-of-sale software uses): for each company register in
EBARIMT_REGISTERS (a JSON list file) — official name, VAT-payer status and date, city-tax status, whether it has
ever issued an e-receipt and the date of the last one (a company that is paid by the state but never issues receipts
is a shell-company signal). Reachable only from Mongolian networks."""
import json
import os

from .core import Http, flat

URL = "https://info.ebarimt.mn/rest/merchant/info"
KEYS = {"taxpayers": ["register"]}
http = Http(rate=1.5, timeout=60, retries=6)


def crawl(ctx):
    regs = json.load(open(os.environ["EBARIMT_REGISTERS"], encoding="utf-8"))
    ctx.expect("taxpayers", len(regs))

    def one(reg):
        o = http.json(URL, params={"regno": reg})
        if not isinstance(o, dict) or not isinstance(o.get("found"), bool):
            raise RuntimeError("ebarimt returned an invalid lookup response")
        ctx.write("taxpayers", {"register": reg, **flat(o if isinstance(o, dict) else {"raw": o})})

    return ctx.run_items("lookups", regs if not ctx.sample else regs[:3], one, workers=2)
