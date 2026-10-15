"""Retail FMV / equity module (spec §9). v1 = manual refresh, date-stamped, staleness-flagged.

`fmv_provider` is the seam where a paid API/scraper can slot in later: anything that
returns rows shaped like RetailFMV dicts works.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import RetailFMV
from .pricing.stats import percentile

TIERS = ("dealer", "private", "wholesale")
# Retail FMV must come from open-market retail, not auction/wholesale sources.
AUCTION_SOURCE_RX = re.compile(r"AUCTION|TAYLOR|T&M|RITCHIE|IRON\s*PLANET|PURPLE\s*WAVE|PUBLIC\s*SURPLUS|MANHEIM|JJ\s*KANE|WHOLESALE", re.I)


def validate_source(tier: str, source: str | None) -> str | None:
    if tier not in TIERS:
        return f"tier must be one of {TIERS}"
    if tier in ("dealer", "private") and source and AUCTION_SOURCE_RX.search(source):
        return "Auction/wholesale sources are excluded from retail FMV — record them as tier 'wholesale' or as sold comps."
    return None


def fmv_row_dict(r: RetailFMV) -> dict:
    return {c.name: getattr(r, c.name) for c in RetailFMV.__table__.columns}


def summarize_fmv(rows: list[dict], spec: dict, settings: dict, today: date | None = None) -> dict | None:
    today = today or date.today()
    mg, yr, miles = spec.get("model_group"), spec.get("year"), spec.get("miles")
    cand = [r for r in rows if r["model_group"] == mg]
    if yr:
        exact = [r for r in cand if r["year_lo"] <= yr <= r["year_hi"]]
        cand = exact or [r for r in cand if r["year_lo"] - 1 <= yr <= r["year_hi"] + 1]
    if not cand:
        return None
    if miles is not None:
        in_band = [r for r in cand if (r.get("miles_lo") is None or r["miles_lo"] <= miles) and
                   (r.get("miles_hi") is None or miles <= r["miles_hi"])]
        if in_band:
            cand = in_band
        else:  # nearest mileage band — mileage is the dominant retail driver
            def dist(r):
                lo = r.get("miles_lo") or 0
                hi = r.get("miles_hi") if r.get("miles_hi") is not None else 10**9
                return 0 if lo <= miles <= hi else min(abs(miles - lo), abs(miles - hi))
            best = min(dist(r) for r in cand)
            cand = [r for r in cand if dist(r) == best]
    disc = 1 - float(settings.get("asking_discount_pct", 10)) / 100
    stale_days = int(settings.get("fmv_stale_days", 45))
    tiers = {}
    for t in TIERS:
        tr = [r for r in cand if r["tier"] == t]
        if not tr:
            continue
        freshest = max(r["as_of_date"] for r in tr)
        cur = [r for r in tr if (freshest - r["as_of_date"]).days <= 30]  # the latest refresh set
        prices = [r["price"] * (disc if r.get("is_asking") else 1) for r in cur]
        age = (today - freshest).days
        tiers[t] = {"value": round(percentile(prices, 50), 0), "n": len(prices), "as_of": freshest.isoformat(),
                    "age_days": age, "stale": age > stale_days,
                    "rows": [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()} for r in cur]}
    if not tiers:
        return None
    want = settings.get("fmv_tier", "private")
    pick = want if want in tiers else ("private" if "private" in tiers else "dealer" if "dealer" in tiers else "wholesale")
    chosen = tiers[pick]
    return {"value": chosen["value"], "tier": pick, "n": chosen["n"], "as_of": chosen["as_of"],
            "age_days": chosen["age_days"], "stale": chosen["stale"], "tiers": tiers,
            "asking_discount_pct": settings.get("asking_discount_pct", 10)}


def fmv_provider(db: Session, tenant_id: int, spec: dict, settings: dict, override: float | None = None,
                 rows: list[dict] | None = None) -> dict | None:
    """rows: preloaded RetailFMV dicts for this model group (bid sheets price hundreds of lots)."""
    if override:
        return {"value": override, "tier": "manual override", "n": 1, "as_of": date.today().isoformat(),
                "age_days": 0, "stale": False, "tiers": {}}
    if rows is None:
        rows = [fmv_row_dict(r) for r in db.scalars(select(RetailFMV).where(
            RetailFMV.tenant_id == tenant_id, RetailFMV.model_group == spec.get("model_group")))]
    return summarize_fmv(rows, spec, settings)
