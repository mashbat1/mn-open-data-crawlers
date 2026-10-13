"""egazar.gov.mn — land parcels from the public GeoServer WFS (AccessConstraints=NONE). Residential parcels
(landuse 22xx) are excluded; the WFS carries no holder names."""
import json
import math

from .core import Http, log

WFS = "https://geoserver.egazar.gov.mn/geoserver/geoware/wfs"
LAYER = "geoware:ca_parcel_tbl"
FILTER = "landuse < 2200 OR landuse >= 2300"
PAGE = 5000
KEYS = {"parcels": ["parcel_id"]}
http = Http(rate=1.0, timeout=600)


def get(start, count):
    return http.json(WFS, params={"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": LAYER,
                                  "outputFormat": "application/json", "count": count, "startIndex": start,
                                  "sortBy": "parcel_id", "CQL_FILTER": FILTER})


def crawl(ctx):
    if "total" not in ctx.state:
        o = get(0, 1)
        ctx.state["total"] = o.get("numberMatched") or o.get("totalFeatures")
        ctx.save()
    total = ctx.state["total"]
    ctx.expect("parcels", total)
    starts = list(range(0, total, PAGE))
    log(f"{total} parcels, {len(starts)} pages")

    def page(start):
        o = get(start, 5 if ctx.sample else PAGE)
        feats = o.get("features") or []
        want = min(PAGE, total - start)
        if not ctx.sample and len(feats) != want:
            raise RuntimeError(f"page {start}: {len(feats)} features, expected {want}")
        for f in feats:
            ctx.write("parcels", {**(f.get("properties") or {}), "feature_id": f.get("id"),
                                  "geometry": json.dumps(f.get("geometry"), separators=(",", ":"))})

    return ctx.run_items("pages", starts, page, workers=2)
