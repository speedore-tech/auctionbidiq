"""Bid recommender (spec §8) — the core deliverable.

Turns a price distribution into target / walk-away / hard-ceiling hammer prices,
converts each to an all-in landed cost, applies an itemized blind-buy reserve,
attaches win probability and equity vs retail. Pure functions; no DB.
"""
from __future__ import annotations

import math

from .geo import lookup_location, road_miles
from .pricing.engine import bid_for_probability, win_probability

THRESHOLDS = ("best_buy", "target", "walk_away", "hard_ceiling")


def _r100(x: float) -> float:
    return float(round(x / 100.0) * 100)


def _floor100(x: float) -> float:
    return float(math.floor(x / 100.0) * 100)


# ------------------------------------------------------------------ cost basis
def resolve_premium(lot: dict, sale: dict | None, settings: dict, override: float | None = None) -> tuple[float, str]:
    if override is not None:
        return float(override), "what-if override"
    if lot.get("buyers_premium_pct") is not None:
        return float(lot["buyers_premium_pct"]), "lot"
    if sale and sale.get("buyers_premium_pct") is not None:
        return float(sale["buyers_premium_pct"]), f"sale {sale.get('sale_id')}"
    return float(settings["default_buyers_premium_pct"]), "tenant default"


def resolve_transport(lot: dict, sale: dict | None, settings: dict, overrides: dict) -> dict:
    mode = overrides.get("transport_mode") or lot.get("transport_mode") or settings["transport_mode"]
    rate = overrides.get("transport_rate")
    if rate is None:
        rate = settings["haul_rate_per_mile"] if mode == "haul" else settings["driveaway_rate_per_mile"]
    home = (settings["home_lat"], settings["home_lon"])
    miles, src = None, None
    if overrides.get("transport_miles") is not None:
        miles, src = float(overrides["transport_miles"]), "what-if override"
    elif lot.get("transport_miles") is not None:
        miles, src = float(lot["transport_miles"]), "lot override"
    else:
        origin = None
        if sale and sale.get("origin_lat") is not None:
            origin, src = (sale["origin_lat"], sale["origin_lon"]), f"sale location {sale.get('location')}"
        else:
            loc = lookup_location(lot.get("location") or (sale or {}).get("location"))
            if loc:
                origin, src = (loc[0], loc[1]), f"location table ({lot.get('location') or (sale or {}).get('location')})"
        miles = road_miles(origin, home, settings["road_factor"])
    if miles is None:
        return {"mode": mode, "rate": rate, "miles": None, "cost": 0.0, "source": "unknown",
                "warning": "Transport origin unknown — enter miles; all-in excludes freight."}
    cost = 0.0 if miles == 0 else max(float(settings["transport_min_charge"]), miles * rate)
    return {"mode": mode, "rate": rate, "miles": miles, "cost": round(cost, 0), "source": src,
            "home": settings.get("home_label")}


def all_in(hammer: float, premium_pct: float, transport: float, fees: float) -> float:
    """all_in = hammer × (1 + buyers_premium) + transport + title/doc fees (spec §8.2)."""
    return hammer * (1 + premium_pct / 100.0) + transport + fees


def hammer_from_all_in(total: float, premium_pct: float, transport: float, fees: float) -> float:
    return max(0.0, (total - transport - fees) / (1 + premium_pct / 100.0))


def reserve_items(lot: dict, settings: dict) -> list[dict]:
    """Itemized blind-buy condition reserve: what the engine-only warranty won't cover (spec §8.3)."""
    rr = settings.get("risk_reserves", {})
    bc = lot.get("body_class")
    items = []
    if bc in ("BOX_TRUCK", "STRAIGHT_TRUCK", "TRACTOR_SLEEPER", "TRACTOR_DAYCAB", "OTHER", None):
        items.append(("dpf_def", "DPF / DEF aftertreatment"))
        items.append(("transmission", "Transmission"))
        if lot.get("liftgate"):
            items.append(("liftgate", "Liftgate"))
    if bc in ("REEFER_TRAILER",):
        items.append(("reefer_unit", "Reefer unit (TK/Carrier)"))
    if bc in ("REEFER_TRAILER", "DRY_VAN_TRAILER"):
        items.append(("tires_brakes", "Tires / brakes"))
    return [{"key": k, "item": label, "amount": float(rr.get(k, 0) or 0)} for k, label in items if rr.get(k)]


# ------------------------------------------------------------------ recommend
def recommend(pred: dict, lot: dict, settings: dict, sale: dict | None = None, fmv: dict | None = None,
              overrides: dict | None = None) -> dict:
    ov = {k: v for k, v in (overrides or {}).items() if v is not None and v != ""}
    if not pred.get("ok"):
        return {"ok": False, "error": pred.get("error"), "flags": pred.get("flags", [])}
    aggr = float(ov.get("aggressiveness", settings["aggressiveness"]))
    aggr = min(1.0, max(0.0, aggr))
    prem, prem_src = resolve_premium(lot, sale, settings, ov.get("buyers_premium_pct"))
    tr = resolve_transport(lot, sale, settings, ov)
    fees = float(ov.get("title_doc_fees", settings["title_doc_fees"]))
    blind = bool(ov.get("blind_buy", lot.get("blind_buy")))
    budget = ov.get("budget_cap_all_in", settings.get("budget_cap_all_in"))
    notes: list[str] = []

    p25, p50, p75, mx = pred["p25"], pred["p50"], pred["p75"], pred["max"]
    best_buy = p25
    target = p25 + aggr * (p50 - p25)
    wb = settings.get("walk_buffer_pct")
    walk = p50 * (1 + float(wb) / 100) if wb not in (None, "") else p75

    ceiling_parts = [{"label": "Market top (" + pred.get("max_source", "cohort max") + ")", "hammer": mx}]
    ceiling = mx
    if budget:
        bh = hammer_from_all_in(float(budget), prem, tr["cost"], fees)
        ceiling_parts.append({"label": f"Budget cap ${float(budget):,.0f} all-in → hammer", "hammer": round(bh, 0)})
        ceiling = min(ceiling, bh)
    res_items = reserve_items(lot, settings) if blind else []
    res_total = sum(i["amount"] for i in res_items)
    if res_total:
        ceiling -= res_total
        ceiling_parts.append({"label": "Blind-buy reserve (itemized)", "hammer": -res_total})
    if ceiling <= 0:
        notes.append("Ceiling is ≤ $0 after reserve/budget — DO NOT BID.")
        ceiling = 0.0
    elif ceiling < best_buy:
        notes.append(f"Ceiling (${ceiling:,.0f}) is below best-buy (${best_buy:,.0f}) after reserve/budget — "
                     "win odds are near zero; inspect in person or pass.")

    hammer = {
        "best_buy": _r100(min(best_buy, ceiling)),
        "target": _r100(min(target, ceiling)),
        "walk_away": _floor100(min(walk, ceiling)),
        "hard_ceiling": _floor100(ceiling),
    }
    # keep ordering monotone after rounding/clamping
    hammer["target"] = min(hammer["target"], hammer["walk_away"])
    hammer["best_buy"] = min(hammer["best_buy"], hammer["target"])
    allin = {k: round(all_in(v, prem, tr["cost"], fees), 0) for k, v in hammer.items()}
    winp = {k: round(win_probability(pred, v), 3) for k, v in hammer.items()}

    equity = None
    if fmv and fmv.get("value"):
        retail = float(fmv["value"])
        equity = {
            "retail": retail, "tier": fmv.get("tier"), "as_of": fmv.get("as_of"), "stale": fmv.get("stale"),
            "by_threshold": {k: {"dollars": round(retail - allin[k], 0),
                                 "pct": round((retail - allin[k]) / retail * 100, 1) if retail else None}
                             for k in THRESHOLDS},
            "allin_pct_of_retail": {k: round(allin[k] / retail * 100, 1) for k in THRESHOLDS} if retail else None,
        }
        if fmv.get("stale"):
            notes.append(f"Retail FMV is stale ({fmv.get('age_days')} days old) — refresh before trusting equity.")

    lo = max(0.0, min(pred["comps"][0]["adjusted_price"] if pred.get("comps") else p25, p25) * 0.8)
    hi = max(mx, p75) * 1.08
    curve = []
    for i in range(25):
        b = lo + (hi - lo) * i / 24
        curve.append({"bid": round(b, 0), "p": round(win_probability(pred, b), 3),
                      "all_in": round(all_in(b, prem, tr["cost"], fees), 0)})
    bfp = {str(int(p * 100)): round(bid_for_probability(pred, p), 0) for p in (0.5, 0.65, 0.8, 0.9)}

    deposit = None
    if sale is not None:
        deposit = {
            "amount": sale.get("deposit_amount") or settings.get("default_deposit_amount"),
            "wire_deadline": sale.get("wire_deadline") or settings.get("default_wire_deadline"),
            "payment_window_hours": sale.get("payment_window_hours") or settings.get("default_payment_window_hours"),
            "absolute": sale.get("absolute", True),
            "terms_notes": sale.get("terms_notes"),
        }

    if tr.get("warning"):
        notes.append(tr["warning"])
    return {
        "ok": True,
        "aggressiveness": aggr,
        "hammer": hammer,
        "all_in": allin,
        "win_prob": winp,
        "win_basis": pred["win_basis"],
        "bid_for_probability": bfp,
        "curve": curve,
        "equity": equity,
        "cost_basis": {"buyers_premium_pct": prem, "premium_source": prem_src, "transport": tr,
                       "title_doc_fees": fees,
                       "formula": "all_in = hammer × (1 + premium) + transport + title/doc fees"},
        "reserve": {"applied": blind, "items": res_items, "total": res_total},
        "ceiling_basis": ceiling_parts,
        "deposit": deposit,
        "notes": notes,
    }


# ------------------------------------------------------------------ multi-lot (spec §8.5)
def plan_multi_lot(items: list[dict], budget_all_in: float, max_wins: int, deescalate_pct: float,
                   results: dict | None = None) -> dict:
    """Sequence rules so bidding on N similar lots can't produce an N-win cash problem.

    items: [{id, lot_no, run_order, premium_pct, transport, fees, target_all_in, ceiling_all_in}]
    results: {id: {"won": bool, "hammer": float}} for lots already sold today.
    """
    results = results or {}
    remaining = float(budget_all_in)
    wins = 0
    factor = 1.0
    plan = []

    def order_key(it):
        lot = str(it.get("lot_no") or "")
        digits = "".join(ch for ch in lot if ch.isdigit())
        return (it.get("run_order") if it.get("run_order") is not None else 10**9, int(digits) if digits else 10**9, lot)

    for it in sorted(items, key=order_key):
        r = results.get(str(it["id"])) or results.get(it["id"])
        row = {"id": it["id"], "lot_no": it.get("lot_no"), "label": it.get("label")}
        if r is not None:
            if r.get("won"):
                cost = all_in(float(r.get("hammer") or 0), it["premium_pct"], it["transport"], it["fees"])
                remaining -= cost
                wins += 1
                factor *= (1 - deescalate_pct / 100.0)
                row.update(action="WON", all_in=round(cost, 0))
            else:
                row.update(action="LOST")
            plan.append(row)
            continue
        if wins >= max_wins:
            row.update(action="SKIP", reason=f"Max wins ({max_wins}) reached")
        else:
            cap = min(it["ceiling_all_in"] * factor, remaining)
            if cap < it["target_all_in"]:
                row.update(action="SKIP", reason=f"Remaining budget ${remaining:,.0f} (de-escalated cap ${cap:,.0f}) "
                                                 f"< target all-in ${it['target_all_in']:,.0f}")
            else:
                max_h = _floor100(hammer_from_all_in(cap, it["premium_pct"], it["transport"], it["fees"]))
                row.update(action="BID", max_all_in=round(cap, 0), max_hammer=max_h,
                           deescalation=round(1 - factor, 3))
        plan.append(row)
    return {"budget_all_in": budget_all_in, "remaining_all_in": round(remaining, 0), "wins": wins,
            "max_wins": max_wins, "deescalate_pct": deescalate_pct, "plan": plan}
