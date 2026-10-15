"""Auction-day analysis: what sold below / above fair value, and which kinds of units do so repeatedly.

Every lot of a past sale is priced the way BidIQ would have priced it the morning of that sale —
using ONLY sales from earlier dates (walk-forward, no peeking) — and compared with its actual hammer.

Verdicts (actual hammer vs the model's distribution for that lot):
  bargain   < p25      sold for less than 3 in 4 comparable units
  below     p25..p50   under fair value
  above     p50..p75   over fair value
  premium   > p75      bid up hard
"""
from __future__ import annotations

import statistics as st
from datetime import date

from .normalize import cross_shop_group
from .pricing.engine import eligible, predict
from .services import normalize_spec

VERDICTS = ("bargain", "below", "above", "premium")
GROUP_KEYS = ("model_group", "body_class", "make", "engine_family", "age_band", "miles_band", "location")


def _age_band(year, sale_date) -> str:
    if not year or not sale_date:
        return "unknown"
    age = sale_date.year - int(year)
    for hi, label in ((2, "0-2 yrs"), (5, "3-5 yrs"), (8, "6-8 yrs"), (12, "9-12 yrs")):
        if age <= hi:
            return label
    return "13+ yrs"


def _miles_band(miles) -> str:
    if miles is None:
        return "no miles"
    for hi, label in ((250_000, "<250k mi"), (500_000, "250-500k mi"), (750_000, "500-750k mi")):
        if miles < hi:
            return label
    return "750k+ mi"


class _History:
    """Comps before a date, indexed like PricingCache so hundreds of lots price quickly."""

    def __init__(self, comps: list[dict]):
        self.by_group: dict[str, list[dict]] = {}
        self.by_cross: dict[str, list[dict]] = {}
        for c in comps:
            self.by_group.setdefault(c.get("model_group") or "", []).append(c)
            xg = cross_shop_group(c.get("model_group"))
            if xg:
                self.by_cross.setdefault(xg, []).append(c)

    def for_spec(self, spec: dict) -> list[dict]:
        own = self.by_group.get(spec.get("model_group") or "", [])
        xg = cross_shop_group(spec.get("model_group"))
        if not xg:
            return own
        seen = {id(c) for c in own}
        return own + [c for c in self.by_cross.get(xg, []) if id(c) not in seen]


def analyze_sale(comps: list[dict], sale_id: str, settings: dict) -> dict:
    lots = [c for c in comps if c.get("sale_id") == sale_id]
    if not lots:
        return {"sale_id": sale_id, "lots": [], "summary": {}, "message": "No result sheet loaded for this sale."}
    sale_date = min((c["sale_date"] for c in lots if isinstance(c.get("sale_date"), date)), default=None)
    hist = _History([c for c in comps if eligible(c) and c.get("sale_id") != sale_id and
                     isinstance(c.get("sale_date"), date) and sale_date and c["sale_date"] < sale_date])
    rows, skipped = [], {"non_op_or_branded": 0, "no_history": 0}
    for c in lots:
        if not eligible(c):
            skipped["non_op_or_branded"] += 1
            continue
        spec = normalize_spec({**c, "sale_date": sale_date})
        pred = predict(spec, hist.for_spec(spec), settings, None)
        if not pred.get("ok"):
            skipped["no_history"] += 1
            continue
        actual = float(c["sold_price"])
        p25, p50, p75 = pred["p25"], pred["p50"], pred["p75"]
        verdict = "bargain" if actual < p25 else "below" if actual < p50 else "above" if actual <= p75 else "premium"
        rows.append({
            "sale_id": sale_id, "lot_no": c.get("lot_no"), "year": c.get("year"), "make": spec.get("make"),
            "model_raw": c.get("model_raw"), "model_group": spec.get("model_group"), "body_class": spec.get("body_class"),
            "engine_family": spec.get("engine_family"), "miles": c.get("miles"), "hours": c.get("hours"),
            "location": c.get("location"), "age_band": _age_band(c.get("year"), sale_date),
            "miles_band": _miles_band(c.get("miles")) if spec.get("body_class") not in ("REEFER_TRAILER", "DRY_VAN_TRAILER", "OTHER") else "trailer",
            "actual": actual, "p25": p25, "fair": p50, "p75": p75,
            "diff": round(actual - p50, 0), "diff_pct": round((actual - p50) / p50 * 100, 1) if p50 else None,
            "verdict": verdict, "confidence": pred["confidence"]["level"],
            "n": pred["methods"].get("A", {}).get("n", 0),
        })
    return {"sale_id": sale_id, "sale_date": sale_date.isoformat() if sale_date else None,
            "location": lots[0].get("location"), "lots": rows, "skipped": skipped, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    diffs = [r["diff_pct"] for r in rows if r["diff_pct"] is not None]
    cnt = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    return {
        "n": len(rows),
        "below_fair_pct": round((cnt["bargain"] + cnt["below"]) / len(rows) * 100, 1),
        "bargain_pct": round(cnt["bargain"] / len(rows) * 100, 1),
        "premium_pct": round(cnt["premium"] / len(rows) * 100, 1),
        "median_diff_pct": round(st.median(diffs), 1) if diffs else None,
        "counts": cnt,
        "total_actual": round(sum(r["actual"] for r in rows), 0),
        "total_fair": round(sum(r["fair"] for r in rows), 0),
    }


def categories(rows: list[dict], key: str, min_n: int = 3) -> list[dict]:
    """Per-category: how often units of this kind sell below fair value, and by how much."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get(key) or "unknown"), []).append(r)
    out = []
    for k, rs in groups.items():
        if len(rs) < min_n:
            continue
        s = summarize(rs)
        out.append({"key": k, **{x: s[x] for x in ("n", "below_fair_pct", "bargain_pct", "premium_pct", "median_diff_pct")},
                    "sales": len({r["sale_id"] for r in rs})})
    # most "buyable" first: sells below fair most often, then by the size of the discount
    return sorted(out, key=lambda r: (-r["below_fair_pct"], r["median_diff_pct"] or 0))


_TREND_CACHE: dict = {}


def analyze_all(comps: list[dict], settings: dict) -> list[dict]:
    """Walk-forward analysis of every loaded sale (cached until the comp set changes)."""
    sig = (len(comps), max((c.get("id") or 0) for c in comps) if comps else 0,
           round(sum(float(c.get("sold_price") or 0) for c in comps)))
    if sig in _TREND_CACHE:
        return _TREND_CACHE[sig]
    sale_ids = sorted({c.get("sale_id") for c in comps if c.get("sale_id")})
    rows: list[dict] = []
    for sid in sale_ids:
        rows += analyze_sale(comps, sid, settings)["lots"]
    _TREND_CACHE.clear()
    _TREND_CACHE[sig] = rows
    return rows


def below_fair_rates(rows: list[dict], min_n: int = 5) -> dict[str, dict]:
    """model_group -> {rate, n, median_diff_pct} for tagging upcoming lots."""
    out = {}
    for c in categories([r for r in rows if r["confidence"] != "Low"], "model_group", min_n=min_n):
        out[c["key"]] = {"rate": c["below_fair_pct"], "n": c["n"], "median_diff_pct": c["median_diff_pct"],
                         "premium_pct": c["premium_pct"]}
    return out
