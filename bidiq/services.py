"""Glue between the DB and the pure pricing / recommender / risk functions."""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .fmv import fmv_provider
from .models import Sale, SoldComp, TargetLot
from .normalize import enrich
from .pricing.engine import predict
from .pricing.stats import percentile
from .recommender import recommend
from .risk import cdl_check, reefer_check, risk_flags, vin_check, warranty_check

COMP_COLS = [c.name for c in SoldComp.__table__.columns]
LOT_COLS = [c.name for c in TargetLot.__table__.columns]
SALE_COLS = [c.name for c in Sale.__table__.columns]


def row_dict(obj, cols) -> dict:
    return {c: getattr(obj, c) for c in cols}


def comp_scope(tenant_id: int, settings: dict):
    if settings.get("include_private_comps", True):
        return or_(SoldComp.tenant_id.is_(None), SoldComp.tenant_id == tenant_id)
    return SoldComp.tenant_id.is_(None)


def load_comps(db: Session, tenant_id: int, settings: dict) -> list[dict]:
    return [row_dict(c, COMP_COLS) for c in db.scalars(select(SoldComp).where(comp_scope(tenant_id, settings)))]


class PricingCache:
    """Per-request cache for pricing many lots at once (a catalog can have 800+ lots).

    Each lot only needs comps from its own model group and its cross-shop group, so comps are
    indexed by both; retail FMV rows and sales are loaded once.
    """

    def __init__(self, db: Session, tenant_id: int, settings: dict):
        from .fmv import fmv_row_dict
        from .models import RetailFMV
        from .normalize import cross_shop_group

        self._xg = cross_shop_group
        self.by_group: dict[str, list[dict]] = {}
        self.by_cross: dict[str, list[dict]] = {}
        for c in load_comps(db, tenant_id, settings):
            self.by_group.setdefault(c.get("model_group") or "", []).append(c)
            xg = cross_shop_group(c.get("model_group"))
            if xg:
                self.by_cross.setdefault(xg, []).append(c)
        self.fmv: dict[str, list[dict]] = {}
        for r in db.scalars(select(RetailFMV).where(RetailFMV.tenant_id == tenant_id)):
            self.fmv.setdefault(r.model_group, []).append(fmv_row_dict(r))
        self.sales = {s.sale_id: row_dict(s, SALE_COLS) for s in db.scalars(select(Sale))}

    def comps_for(self, spec: dict) -> list[dict]:
        own = self.by_group.get(spec.get("model_group") or "", [])
        xg = self._xg(spec.get("model_group"))
        if not xg:
            return own
        seen = {id(c) for c in own}
        return own + [c for c in self.by_cross.get(xg, []) if id(c) not in seen]


def sale_dict(db: Session, sale_id: str | None) -> dict | None:
    if not sale_id:
        return None
    s = db.scalar(select(Sale).where(Sale.sale_id == sale_id))
    return row_dict(s, SALE_COLS) if s else None


SPEC_INPUT_KEYS = ("year", "make", "model_raw", "model_group", "class_raw", "body_class", "engine_raw", "engine_family",
                   "desc_raw", "trans_raw", "transmission", "miles", "hours", "hours_metric", "door_type",
                   "body_length_ft", "branded_title", "non_operable", "lot_type", "tamper_flag", "sale_date",
                   "location", "sale_id", "lot_no", "sleeper")


def normalize_spec(spec: dict) -> dict:
    s = {k: spec.get(k) for k in SPEC_INPUT_KEYS if spec.get(k) not in (None, "")}
    for k in ("year", "miles"):
        if s.get(k) is not None:
            s[k] = int(float(s[k]))
    if s.get("hours") is not None:
        s["hours"] = float(s["hours"])
    if isinstance(s.get("sale_date"), str):
        try:
            s["sale_date"] = date.fromisoformat(s["sale_date"])
        except ValueError:
            s.pop("sale_date")
    enrich(s)
    return s


def evaluate(db: Session, tenant_id: int, settings: dict, lot: dict, overrides: dict | None = None,
             comps: list[dict] | None = None, cache: PricingCache | None = None) -> dict:
    """Full recommendation for one lot (TargetLot dict or ad-hoc spec)."""
    overrides = dict(overrides or {})
    lot = dict(lot)
    if overrides.get("miles") not in (None, ""):
        lot["miles"] = int(float(overrides["miles"]))
    if overrides.get("hours") not in (None, ""):
        lot["hours"] = float(overrides["hours"])
    sale = cache.sales.get(lot.get("sale_id")) if cache else sale_dict(db, lot.get("sale_id"))
    if sale and not lot.get("sale_date"):
        lot["sale_date"] = sale.get("sale_date")
    if sale and not lot.get("location"):
        lot["location"] = sale.get("location")
    spec = normalize_spec(lot)
    for k in ("model_group", "super_group", "body_class", "engine_family", "transmission", "make"):
        if spec.get(k) and not lot.get(k):
            lot[k] = spec[k]
    if cache is not None:
        comps = cache.comps_for(spec)
    elif comps is None:
        comps = load_comps(db, tenant_id, settings)
    fmv = fmv_provider(db, tenant_id, spec, settings, override=lot.get("retail_override"),
                       rows=cache.fmv.get(spec.get("model_group"), []) if cache else None)
    pred = predict(spec, comps, settings, fmv)
    rec = recommend(pred, lot, settings, sale, fmv, overrides)
    sale_abs = sale.get("absolute", True) if sale else True
    base_flags = risk_flags(lot, settings, sale_abs)
    have = {f["code"] for f in base_flags}
    if lot.get("non_operable") and rec.get("ok"):
        rec["notes"].insert(0, "Non-runner: these prices are for a running unit — knock off your repair estimate "
                               "before bidding.")
    risk = {
        "flags": base_flags + [{"code": f, "severity": "medium", "message": _flag_msg(f)}
                               for f in pred.get("flags", []) if f not in have],
        "cdl": cdl_check(lot.get("gvwr"), lot.get("rear_axle_lbs"), spec.get("body_class"),
                         bool((lot.get("checklist") or {}).get("gvwr_confirmed"))),
        "vin": vin_check(lot.get("vin"), spec.get("year"), spec.get("make")) if lot.get("vin") else
        vin_check(None),
        "warranty": warranty_check(spec.get("year"), spec.get("miles"), lot.get("gvwr"), spec.get("body_class"),
                                   settings, spec.get("sale_date")),
    }
    if spec.get("body_class") == "REEFER_TRAILER":
        risk["reefer"] = reefer_check({**lot, **spec}, settings)
    return {"spec": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in spec.items()},
            "prediction": _strip(pred), "recommendation": rec, "risk": risk, "fmv": fmv, "sale": _jsonable(sale)}


def _flag_msg(code: str) -> str:
    return {
        "NEWER_THAN_ANY_COMP": "Newer than any comp — price extrapolated from retail-minus; LOW confidence.",
        "NO_FAMILY_COMPS": "No comps for this family — retail-minus fallback only.",
        "ODOM_ECM_MISMATCH": "Odometer/ECM mismatch lowers confidence.",
    }.get(code, code)


def _strip(pred: dict) -> dict:
    p = dict(pred)
    p.pop("win_values", None)
    p.pop("win_weights", None)
    return p


def _jsonable(d: dict | None):
    if d is None:
        return None
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in d.items()}


def summary_row(lot: dict, ev: dict) -> dict:
    """Compact bid-sheet row."""
    rec, pred = ev["recommendation"], ev["prediction"]
    out = {
        "id": lot.get("id"), "sale_id": lot.get("sale_id"), "lot_no": lot.get("lot_no"), "year": lot.get("year"),
        "make": lot.get("make"), "model_raw": lot.get("model_raw"), "model_group": ev["spec"].get("model_group"),
        "body_class": ev["spec"].get("body_class"), "engine_family": ev["spec"].get("engine_family"),
        "miles": lot.get("miles"), "hours": lot.get("hours"), "gvwr": lot.get("gvwr"), "vin": lot.get("vin"),
        "body_length_ft": lot.get("body_length_ft"), "status": lot.get("status"),
        "cdl": ev["risk"]["cdl"]["message"], "cdl_required": ev["risk"]["cdl"].get("cdl_required"),
        "cdl_status": ev["risk"]["cdl"]["status"],
        # ABSOLUTE_SALE / NO_VIN apply to nearly every T&M lot — keep them on the lot page, not the list
        "flags": [f["code"] for f in ev["risk"]["flags"] if f["code"] not in ("ABSOLUTE_SALE", "NO_VIN")],
        "ok": rec.get("ok", False),
    }
    if rec.get("ok"):
        out.update({
            "hammer": rec["hammer"], "all_in": rec["all_in"], "win_prob": rec["win_prob"],
            "p50": pred["p50"], "n": pred["methods"].get("A", {}).get("n", 0),
            "confidence": pred["confidence"]["level"],
            "equity_target": (rec["equity"] or {}).get("by_threshold", {}).get("target"),
            "retail": (rec["equity"] or {}).get("retail"),
        })
    else:
        out["error"] = rec.get("error")
    return out


# ------------------------------------------------------------------ backtest (spec §12)
def backtest(comps: list[dict], settings: dict, sale_id: str | None = None, limit: int = 300) -> dict:
    """Walk-forward: price each comp using only comps from strictly earlier sale dates."""
    def key(c):
        return c.get("sale_date") or date.min

    pool = [c for c in comps if not c.get("non_operable") and not c.get("branded_title") and c.get("sold_price")]
    tests = [c for c in pool if (sale_id is None or c.get("sale_id") == sale_id)]
    tests = sorted(tests, key=key, reverse=True)[:limit]
    rows = []
    for c in tests:
        history = [h for h in pool if key(h) < key(c)]
        if not history:
            continue
        spec = normalize_spec({**c})
        pred = predict(spec, history, settings, None)
        if not pred.get("ok"):
            continue
        actual = float(c["sold_price"])
        rec = recommend(pred, {**c, "blind_buy": False}, settings, None, None, {"transport_miles": 0})
        rows.append({
            "sale_id": c.get("sale_id"), "lot_no": c.get("lot_no"), "year": c.get("year"),
            "model_group": c.get("model_group"), "miles": c.get("miles"), "actual": actual,
            "p25": pred["p25"], "p50": pred["p50"], "p75": pred["p75"],
            "ceiling": rec["hammer"]["hard_ceiling"], "target": rec["hammer"]["target"],
            "confidence": pred["confidence"]["level"],
            "err": round(pred["p50"] - actual, 0),
        })
    if not rows:
        return {"n": 0, "rows": [], "message": "Not enough history: need comps from at least two sale dates."}
    errs = [abs(r["err"]) for r in rows]
    ape = [abs(r["err"]) / r["actual"] * 100 for r in rows if r["actual"]]
    by_conf = {}
    for lvl in ("High", "Med", "Low"):
        sub = [r for r in rows if r["confidence"] == lvl]
        if sub:
            by_conf[lvl] = {"n": len(sub), "mape": round(sum(abs(r["err"]) / r["actual"] * 100 for r in sub) / len(sub), 1)}
    return {
        "n": len(rows),
        "mae": round(sum(errs) / len(errs), 0),
        "median_abs_err": round(percentile(errs, 50), 0),
        "mape": round(sum(ape) / len(ape), 1) if ape else None,
        "coverage_p25_p75": round(sum(1 for r in rows if r["p25"] <= r["actual"] <= r["p75"]) / len(rows) * 100, 1),
        "won_at_target_pct": round(sum(1 for r in rows if r["actual"] <= r["target"]) / len(rows) * 100, 1),
        "within_ceiling_pct": round(sum(1 for r in rows if r["actual"] <= r["ceiling"]) / len(rows) * 100, 1),
        "by_confidence": by_conf,
        "rows": rows,
    }
