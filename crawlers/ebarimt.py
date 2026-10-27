"""ebarimt.mn public taxpayer lookup (the one point-of-sale software uses): for each company register in
EBARIMT_REGISTERS (a JSON list file) — official name, VAT-payer status and date, city-tax status, whether it has
ever issued an e-receipt and the date of the last one (a company that is paid by the state but never issues receipts
is a shell-company signal). Reachable only from Mongolian networks."""
import json
import os

from .core import Http, flat, TimeBudgetExpired

URL = "https://info.ebarimt.mn/rest/merchant/info"
KEYS = {"taxpayers": ["register"]}
http = Http(rate=1.5, timeout=30, retries=1)
TIN_URL = "https://api.ebarimt.mn/api/info/check/getTinInfo"
PROFILE_URL = "https://api.ebarimt.mn/api/info/check/getInfo"


def lookup(reg):
    try:
        body = http.json(URL, params={"regno": reg})
        if not isinstance(body, dict) or not isinstance(body.get("found"), bool):
            raise RuntimeError("ebarimt returned an invalid legacy lookup response")
        return body
    except TimeBudgetExpired:
        raise
    except RuntimeError as exc:
        legacy_error = str(exc)[:250]
    resolved = http.json(TIN_URL, params={"regNo": reg})
    if not isinstance(resolved, dict):
        raise RuntimeError("Invalid official TIN lookup response")
    common = {"legacy_lookup_available": False, "legacy_error": legacy_error,
              "lookup_source": TIN_URL, "tin_lookup": resolved, "receipt_status_known": False}
    if resolved.get("data") is None and resolved.get("msg", "").strip() == "Хэрэглэгчийн мэдээлэл олдсонгүй.":
        # This answers only TIN availability; it cannot establish the legacy
        # merchant/receipt fields. Keep those unknown rather than setting false.
        return {**common, "found": None, "tin_found": False, "lookup_status": "no_tax_registry_mapping"}
    tin = resolved.get("data")
    if resolved.get("status") != 200 or not str(tin).isdigit():
        raise RuntimeError("TIN lookup failed without an explicit not-found result")
    result = http.json(PROFILE_URL, params={"tin": tin})
    profile = result.get("data") if isinstance(result, dict) and result.get("status") == 200 else None
    if not isinstance(profile, dict) or not isinstance(profile.get("found"), bool):
        raise RuntimeError("Official taxpayer profile lookup failed")
    return {**common, "found": profile["found"], "found_scope": "tax_registry", "tin_found": True,
            "tin": str(tin), "name": profile.get("name"), "vatpayer": profile.get("vatPayer"),
            "citypayer": profile.get("cityPayer"), "vatpayerRegisteredDate": profile.get("vatpayerRegisteredDate"),
            "tax_profile": profile, "lookup_status": "tax_registry_profile"}


def crawl(ctx):
    regs = json.load(open(os.environ["EBARIMT_REGISTERS"], encoding="utf-8"))
    ctx.expect("taxpayers", len(regs))
    if ctx.state.get("lookup_schema_version", 0) < 2:
        ctx.state["lookup_schema_version"] = 2
        ctx.state["legacy_lookup_prior_errors"] = dict(ctx.state.get("errors", {}).get("lookups", {}))
        for reg in ctx.state["legacy_lookup_prior_errors"]:
            ctx.state["attempts"].setdefault("lookups", {})[reg] = 0
    fallbacks = ctx.state.setdefault("fallback_registers", {})

    def one(reg):
        o = lookup(reg)
        if o.get("legacy_lookup_available") is False:
            fallbacks[reg] = o["lookup_status"]
        else:
            fallbacks.pop(reg, None)
        ctx.write("taxpayers", {"register": reg, **flat(o)})

    complete = ctx.run_items("lookups", regs if not ctx.sample else regs[:3], one, workers=2)
    # The register list is refreshed between runs; registers looked up from an earlier list are rows too.
    if not ctx.sample:
        ctx.expect("taxpayers", len(ctx.done_set("lookups") | set(regs)))
    ctx.state["coverage_scope"] = {"registers": len(regs), "legacy_unavailable_count": len(fallbacks),
                                    "fallback": "official TIN/taxpayer registry; receipt fields remain unknown"}
    return complete
