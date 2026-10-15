"""Lock a sale's bids before the auction, then score them against the actual hammers afterwards.

The lock is a frozen copy of what the model said (stored on each lot's `last_recommendation`), so
uploading that sale's own results later can't leak into the numbers being judged.
"""
from __future__ import annotations

import statistics as st
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .ingest.tm_formats import location_key
from .models import SoldComp, TargetLot
from .services import LOT_COLS, PricingCache, evaluate, row_dict


def lock_sale(db: Session, tenant_id: int, settings: dict, sale_id: str, user_email: str) -> dict:
    cache = PricingCache(db, tenant_id, settings)
    locked = skipped = 0
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    for t in db.scalars(select(TargetLot).where(TargetLot.tenant_id == tenant_id, TargetLot.sale_id == sale_id)):
        if t.status == "scratched":
            skipped += 1
            continue
        ev = evaluate(db, tenant_id, settings, row_dict(t, LOT_COLS), cache=cache)
        p, r = ev["prediction"], ev["recommendation"]
        if not r.get("ok"):
            skipped += 1
            continue
        t.last_recommendation = {
            "locked": True, "locked_at": now, "locked_by": user_email,
            "p25": p["p25"], "p50": p["p50"], "p75": p["p75"],
            "hammer": r["hammer"], "all_in": r["all_in"], "win_prob": r["win_prob"],
            "confidence": p["confidence"]["level"], "n": p["methods"].get("A", {}).get("n", 0),
            "premium_pct": r["cost_basis"]["buyers_premium_pct"], "transport": r["cost_basis"]["transport"]["cost"],
            "miles": t.miles, "hours": t.hours,
        }
        locked += 1
    db.commit()
    return {"sale_id": sale_id, "locked": locked, "skipped": skipped, "locked_at": now}


def _outcome(actual: float, h: dict) -> str:
    if actual <= h["target"]:
        return "target"          # our target bid would have won it
    if actual <= h["walk_away"]:
        return "walk"
    if actual <= h["hard_ceiling"]:
        return "ceiling"
    return "over"                # sold above our ceiling — we'd have (correctly) walked


def scorecard(db: Session, tenant_id: int, sale_id: str) -> dict:
    lots = db.scalars(select(TargetLot).where(TargetLot.tenant_id == tenant_id, TargetLot.sale_id == sale_id)).all()
    if not lots:
        return {"sale_id": sale_id, "rows": [], "summary": {}, "message": "No lots for this sale."}
    sale_date = next((t.sale_date for t in lots if t.sale_date), None)
    key = location_key(next((t.location for t in lots if t.location), None))
    # results for this sale: same sale id, or (if the catalog was filed under another id) same date + place
    conds = [SoldComp.sale_id == sale_id]
    if sale_date:
        conds.append(SoldComp.sale_date == sale_date)
    sold = {}
    for c in db.scalars(select(SoldComp).where(or_(SoldComp.tenant_id.is_(None), SoldComp.tenant_id == tenant_id),
                                              or_(*conds))):
        if c.sale_id == sale_id or key is None or location_key(c.location) == key:
            if c.lot_no not in sold or c.tenant_id is not None:   # a private copy wins
                sold[c.lot_no] = c
    rows = []
    for t in lots:
        snap = t.last_recommendation or {}
        c = sold.get(t.lot_no)
        row = {"id": t.id, "lot_no": t.lot_no, "year": t.year, "make": t.make, "model_raw": t.model_raw,
               "model_group": t.model_group, "engine_family": t.engine_family, "miles": t.miles,
               "status": t.status, "locked": bool(snap.get("locked")), "actual": None}
        if snap.get("locked"):
            row.update({k: snap.get(k) for k in ("p25", "p50", "p75", "hammer", "all_in", "confidence", "n", "locked_at")})
        if c is not None:
            row["actual"] = c.sold_price
            row["actual_non_op"] = bool(c.non_operable or c.branded_title)
        if row["actual"] is not None and row["locked"]:
            a = float(row["actual"])
            row["err"] = round(row["p50"] - a, 0)
            row["err_pct"] = round((row["p50"] - a) / a * 100, 1) if a else None
            row["in_range"] = row["p25"] <= a <= row["p75"]
            row["outcome"] = _outcome(a, row["hammer"])
        rows.append(row)
    scored = [r for r in rows if r.get("outcome") and not r.get("actual_non_op")]
    summary = {"lots": len(rows), "locked": sum(r["locked"] for r in rows),
               "results_found": sum(r["actual"] is not None for r in rows), "scored": len(scored)}
    if scored:
        summary.update({
            "median_abs_err_pct": round(st.median(abs(r["err_pct"]) for r in scored if r["err_pct"] is not None), 1),
            "median_err_pct": round(st.median(r["err_pct"] for r in scored if r["err_pct"] is not None), 1),
            "in_range_pct": round(sum(r["in_range"] for r in scored) / len(scored) * 100, 1),
            "won_at_target": sum(r["outcome"] == "target" for r in scored),
            "won_at_walk": sum(r["outcome"] in ("target", "walk") for r in scored),
            "won_at_ceiling": sum(r["outcome"] != "over" for r in scored),
            "over_ceiling": sum(r["outcome"] == "over" for r in scored),
            "by_confidence": {lvl: round(st.median(abs(r["err_pct"]) for r in scored if r["confidence"] == lvl), 1)
                              for lvl in ("High", "Med", "Low") if any(r["confidence"] == lvl for r in scored)},
        })
    return {"sale_id": sale_id, "sale_date": sale_date.isoformat() if sale_date else None, "rows": rows, "summary": summary}
