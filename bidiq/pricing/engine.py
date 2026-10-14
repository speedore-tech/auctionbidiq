"""Pricing / prediction engine (spec §7).

Produces a price *distribution* for any target spec by blending:
  A. cohort percentiles (always available; auto-widening, class-aware)
  B. family-level OLS regression (when N sufficient) -> also used to adjust comps to target spec
  C. retail-minus fallback (sparse / newer-than-any-comp)
Confidence is reported separately from price and never moves the point estimate.

Everything operates on plain dicts so it can be unit-tested and backtested without a DB.
"""
from __future__ import annotations

from datetime import date

from ..normalize import cross_shop_group, engine_weight, super_group
from .stats import OLS, ecdf, norm_cdf, percentile

TRACTOR_CLASSES = {"TRACTOR_SLEEPER", "TRACTOR_DAYCAB"}
TRAILER_CLASSES = {"REEFER_TRAILER", "DRY_VAN_TRAILER"}

MILES_STEPS = [(1, 50_000), (2, 75_000), (2, 100_000), (3, 150_000), (4, 250_000), (5, None), (None, None)]
HOURS_STEPS = [(1, 1_500), (2, 2_500), (2, 4_000), (3, 6_000), (4, 10_000), (5, None), (None, None)]
RELAXED_STEPS = [(2, 100_000), (4, 250_000), (None, None)]
RELAXED_HOURS_STEPS = [(2, 4_000), (4, 10_000), (None, None)]


# ------------------------------------------------------------------ helpers
def eligible(c: dict) -> bool:
    """Non-op and branded units are a different market: excluded from stats by default (spec §6)."""
    return not c.get("non_operable") and not c.get("branded_title") and (c.get("sold_price") or 0) > 0


def usage_kind(body_class: str | None) -> str:
    if body_class == "REEFER_TRAILER":
        return "hours"
    if body_class == "DRY_VAN_TRAILER":
        return "none"
    return "miles"


def usage_of(rec: dict, kind: str, metric: str | None = None) -> float | None:
    if kind == "miles":
        return rec.get("miles")
    if kind == "hours":
        # Total vs Engine vs Electric hours are not comparable — only same-metric matches.
        if rec.get("hours") is None:
            return None
        if metric and rec.get("hours_metric") and rec.get("hours_metric") != metric:
            return None
        return rec.get("hours")
    return None


def target_age(spec: dict) -> float | None:
    if not spec.get("year"):
        return None
    sd = spec.get("sale_date")
    yr = sd.year if isinstance(sd, date) else date.today().year
    return max(0.5, yr - int(spec["year"]) + 0.5)


def comp_age(c: dict) -> float | None:
    if c.get("age_years") is not None:
        return c["age_years"]
    if not c.get("year"):
        return None
    sd = c.get("sale_date")
    yr = sd.year if isinstance(sd, date) else date.today().year
    return max(0.5, yr - int(c["year"]) + 0.5)


def _scale(kind: str) -> float:
    return 100_000.0 if kind == "miles" else 1_000.0


# ------------------------------------------------------------------ Method B
def fit_regression(spec: dict, comps: list[dict]) -> dict | None:
    """Family-level OLS: price ≈ b0 + b_age·age + b_use·usage [+ b_eng·engine_weight]."""
    kind = usage_kind(spec.get("body_class"))
    metric = spec.get("hours_metric") if kind == "hours" else None
    fam_key = "model_group"
    fam = [c for c in comps if eligible(c) and c.get("model_group") == spec.get("model_group")]
    sg = cross_shop_group(spec.get("model_group"))
    if len(fam) < 12 and sg:
        fam = [c for c in comps if eligible(c) and cross_shop_group(c.get("model_group")) == sg]
        fam_key = "super_group"
    fam = [c for c in fam if comp_age(c) is not None]
    if len(fam) < 8:
        return None

    with_use = [c for c in fam if usage_of(c, kind, metric) is not None] if kind != "none" else []
    use_usage = kind != "none" and len(with_use) >= max(8, 0.6 * len(fam)) and \
        usage_of(spec, kind, metric) is not None
    rows = with_use if use_usage else fam
    weights = [engine_weight(c.get("engine_family")) for c in rows]
    use_eng = len(set(round(w, 2) for w in weights)) > 1 and spec.get("body_class") in TRACTOR_CLASSES | {"BOX_TRUCK", "STRAIGHT_TRUCK"}

    def design(features: list[str]):
        X, y = [], []
        for c in rows:
            x = [1.0]
            if "age" in features:
                x.append(comp_age(c))
            if "usage" in features:
                x.append(usage_of(c, kind, metric) / _scale(kind))
            if "eng" in features:
                x.append(engine_weight(c.get("engine_family")))
            X.append(x)
            y.append(float(c["sold_price"]))
        return X, y

    features = ["age"] + (["usage"] if use_usage else []) + (["eng"] if use_eng else [])
    model = None
    for _ in range(4):
        while features and len(rows) < max(8, 3 * (len(features) + 1)):
            features.pop()  # drop least-essential covariate (eng, then usage) when data is thin
        if not features:
            return None
        X, y = design(features)
        try:
            model = OLS(X, y)
        except ValueError:
            return None
        # Depreciation must be non-positive; drop a counter-intuitive covariate and refit.
        bad = [f for f, b in zip(features, model.coef[1:]) if f in ("age", "usage") and b > 0]
        if not bad:
            break
        features = [f for f in features if f not in bad]
        if not features:
            return None
    if model is None:
        return None

    coefs = dict(zip(["intercept"] + features, model.coef))
    x_t = [1.0]
    t_age = target_age(spec)
    if "age" in features:
        x_t.append(t_age)
    if "usage" in features:
        x_t.append(usage_of(spec, kind, metric) / _scale(kind))
    if "eng" in features:
        x_t.append(engine_weight(spec.get("engine_family")))
    point = model.predict_point(x_t)
    se = model.predict_se(x_t)
    return {
        "family": spec.get("model_group") if fam_key == "model_group" else sg,
        "family_level": fam_key,
        "n": model.n,
        "features": features,
        "coefs": coefs,
        "r2": round(model.r2, 3),
        "sigma": round(model.sigma, 0),
        "point": point,
        "se": se,
        "p25": max(0.0, point - 0.674 * se),
        "p50": max(0.0, point),
        "p75": max(0.0, point + 0.674 * se),
        "usage_kind": kind,
        "usage_metric": metric,
        "dollars_per_year": round(-coefs.get("age", 0.0), 0) if "age" in coefs else None,
        "dollars_per_100k_miles": round(-coefs["usage"], 0) if "usage" in coefs and kind == "miles" else None,
        "dollars_per_1k_hours": round(-coefs["usage"], 0) if "usage" in coefs and kind == "hours" else None,
    }


def adjust_price(c: dict, spec: dict, reg: dict | None) -> float:
    """Move a comp's hammer to the target's age/usage/engine using regression slopes."""
    price = float(c["sold_price"])
    if not reg:
        return price
    co = reg["coefs"]
    kind, metric = reg["usage_kind"], reg["usage_metric"]
    ta, ca = target_age(spec), comp_age(c)
    if "age" in co and ta is not None and ca is not None:
        price += co["age"] * (ta - ca)
    if "usage" in co:
        tu, cu = usage_of(spec, kind, metric), usage_of(c, kind, metric)
        if tu is not None and cu is not None:
            price += co["usage"] * (tu - cu) / _scale(kind)
    if "eng" in co:
        price += co["eng"] * (engine_weight(spec.get("engine_family")) - engine_weight(c.get("engine_family")))
    return max(0.0, price)


# ------------------------------------------------------------------ Method A
def build_cohort(spec: dict, comps: list[dict], min_n: int) -> dict:
    kind = usage_kind(spec.get("body_class"))
    metric = spec.get("hours_metric") if kind == "hours" else None
    t_use = usage_of(spec, kind, metric) if kind != "none" else None
    year = spec.get("year")
    bc = spec.get("body_class")
    pool = [c for c in comps if eligible(c)]
    notes: list[str] = []
    if kind != "none" and t_use is None:
        notes.append(f"Target {kind} unknown — usage band not applied (enter {kind} to tighten the cohort).")

    def match(c, scope, yb, ub):
        if scope == "exact":
            if c.get("model_group") != spec.get("model_group"):
                return False
            if bc in TRACTOR_CLASSES and spec.get("engine_family") and c.get("engine_family") \
                    and c["engine_family"] != spec["engine_family"]:
                return False  # tractors: e.g. A26 vs X15 price differently
        elif scope == "super":
            sg = cross_shop_group(spec.get("model_group"))
            if not sg or cross_shop_group(c.get("model_group")) != sg:
                return False
        elif scope == "mixed_engine":
            if c.get("model_group") != spec.get("model_group"):
                return False
        if bc == "REEFER_TRAILER" and spec.get("door_type") and c.get("door_type") and c["door_type"] != spec["door_type"]:
            return False
        if yb is not None and year and c.get("year") and abs(int(c["year"]) - int(year)) > yb:
            return False
        if ub is not None and t_use is not None:
            cu = usage_of(c, kind, metric)
            if cu is None or abs(cu - t_use) > ub:
                return False
        return True

    steps_main = HOURS_STEPS if kind == "hours" else MILES_STEPS
    steps_rel = RELAXED_HOURS_STEPS if kind == "hours" else RELAXED_STEPS
    schedule = [("exact", yb, ub) for yb, ub in steps_main]
    if bc in TRACTOR_CLASSES and spec.get("engine_family"):
        schedule += [("mixed_engine", yb, ub) for yb, ub in steps_rel]
    if cross_shop_group(spec.get("model_group")):
        schedule += [("super", yb, ub) for yb, ub in steps_rel]

    best = None
    for idx, (scope, yb, ub) in enumerate(schedule):
        sel = [c for c in pool if match(c, scope, yb, ub)]
        cand = {"step": idx, "scope": scope, "year_band": yb, "usage_band": ub if t_use is not None else None,
                "comps": sel, "steps_total": len(schedule)}
        if best is None or len(sel) > len(best["comps"]):
            best = cand
        if len(sel) >= min_n:
            best = cand
            break
    best = best or {"step": len(schedule), "scope": "none", "year_band": None, "usage_band": None, "comps": [],
                    "steps_total": len(schedule)}
    if best["scope"] == "super":
        notes.append("Cohort widened to the cross-shop super-group (e.g. M2 106 / MV607 / 4300) — model identity kept in drill-down.")
    if best["scope"] == "mixed_engine":
        notes.append("Cohort mixes engine families — engines price differently; confidence reduced.")
    best["notes"] = notes
    best["usage_kind"] = kind
    best["usage_metric"] = metric
    return best


def band_label(cohort: dict) -> str:
    yb, ub, kind = cohort.get("year_band"), cohort.get("usage_band"), cohort.get("usage_kind")
    y = f"±{yb} yr" if yb is not None else "any year"
    if kind == "none":
        u = ""
    elif ub is None:
        u = f", any {kind}"
    else:
        u = f", ±{ub:,.0f} {'mi' if kind == 'miles' else 'hrs'}"
    scope = {"exact": "same model", "super": "super-group", "mixed_engine": "same model, any engine"}.get(cohort["scope"], cohort["scope"])
    return f"{scope}, {y}{u}"


# ------------------------------------------------------------------ Blend
def predict(spec: dict, comps: list[dict], settings: dict, fmv: dict | None = None) -> dict:
    """Return the full price distribution + provenance for a target spec."""
    min_n = int(settings.get("min_cohort_n", 5))
    spec = dict(spec)
    spec.setdefault("super_group", super_group(spec.get("model_group")))
    flags: list[str] = []
    assumptions: list[str] = []

    reg = fit_regression(spec, comps)
    cohort = build_cohort(spec, comps, min_n)
    cc = cohort["comps"]

    # Newer-than-any-comp -> extrapolation (spec §7.3, §15)
    xg = cross_shop_group(spec.get("model_group"))
    fam_years = [c["year"] for c in comps if eligible(c) and c.get("year") and
                 (c.get("model_group") == spec.get("model_group") or
                  (xg and cross_shop_group(c.get("model_group")) == xg))]
    extrapolated = bool(spec.get("year") and fam_years and int(spec["year"]) > max(fam_years))
    if spec.get("year") and not fam_years:
        extrapolated = True
    if extrapolated:
        flags.append("NEWER_THAN_ANY_COMP" if fam_years else "NO_FAMILY_COMPS")

    adjusted = reg is not None and len(cc) > 0
    comp_rows = []
    for c in cc:
        adj = adjust_price(c, spec, reg) if adjusted else float(c["sold_price"])
        comp_rows.append({**_comp_public(c), "adjusted_price": round(adj, 0)})
    values = [r["adjusted_price"] for r in comp_rows]
    raw_values = [float(c["sold_price"]) for c in cc]

    methods: dict = {}
    if values:
        methods["A"] = {
            "label": "Cohort percentiles" + (" (comps adjusted to target age/usage)" if adjusted else ""),
            "n": len(values),
            "band": band_label(cohort),
            "widen_step": cohort["step"],
            "p25": percentile(values, 25), "p50": percentile(values, 50), "p75": percentile(values, 75),
            "min": min(values), "max": max(values),
            "raw_p50": percentile(raw_values, 50), "raw_max": max(raw_values),
        }
    if reg:
        methods["B"] = {"label": f"OLS regression on {reg['family']} ({reg['family_level']})", **reg}
    if fmv and fmv.get("value"):
        v = fmv["value"]
        methods["C"] = {
            "label": f"Retail-minus: {fmv.get('tier', 'retail')} FMV × auction discount",
            "retail": v,
            "p25": v * settings["auction_discount_low"],
            "p50": v * settings["auction_discount_factor"],
            "p75": v * settings["auction_discount_high"],
            "fmv": fmv,
        }

    # weights by data density
    w = {}
    if "A" in methods:
        n = methods["A"]["n"]
        w["A"] = min(1.0, n / 10) / (1 + 0.25 * cohort["step"])
    if "B" in methods:
        w["B"] = 0.8 * min(1.0, reg["n"] / 25)
    if "C" in methods:
        w["C"] = 1.0 if (extrapolated or not w) else 0.15
        if extrapolated:
            for k in ("A", "B"):
                if k in w:
                    w[k] *= 0.3
    if not w:
        return {"ok": False, "error": "No comps for this model family and no retail FMV on file. "
                "Ingest result sheets for this family or enter retail FMV comps.", "flags": flags}
    tot = sum(w.values())
    w = {k: v / tot for k, v in w.items()}
    blend = {q: sum(w[k] * methods[k][q] for k in w) for q in ("p25", "p50", "p75")}

    if "A" in methods:
        ceiling_market = methods["A"]["max"]
        ceiling_src = "max of cohort (adjusted to target spec)" if adjusted else "max of cohort (proven market top)"
    elif "C" in methods:
        ceiling_market = methods["C"]["p75"]
        ceiling_src = "retail × high auction-discount (assumption — no comps)"
        assumptions.append(ceiling_src)
    else:
        ceiling_market = blend["p75"] + 0.674 * (reg["se"] if reg else 0)
        ceiling_src = "regression p90 (no cohort)"
    # never let quantiles cross
    blend["p25"] = min(blend["p25"], blend["p50"])
    blend["p75"] = max(blend["p75"], blend["p50"])

    # win-probability basis
    empirical = len(values) >= min_n and not extrapolated
    sigma = max(1.0, (blend["p75"] - blend["p25"]) / 1.349)

    conf = confidence(spec, cohort, methods, extrapolated, min_n)
    if spec.get("tamper_flag"):
        flags.append("ODOM_ECM_MISMATCH")

    return {
        "ok": True,
        "spec": {k: spec.get(k) for k in ("year", "make", "model_group", "super_group", "body_class", "engine_family",
                                          "miles", "hours", "hours_metric", "door_type")},
        "p25": round(blend["p25"], 0),
        "p50": round(blend["p50"], 0),
        "p75": round(blend["p75"], 0),
        "max": round(ceiling_market, 0),
        "max_source": ceiling_src,
        "weights": {k: round(v, 3) for k, v in w.items()},
        "methods": methods,
        "cohort_notes": cohort["notes"],
        "comps": sorted(comp_rows, key=lambda r: r["adjusted_price"]),
        "win_basis": "empirical" if empirical else "modelled",
        "win_values": values if empirical else None,
        "win_sigma": sigma,
        "confidence": conf,
        "extrapolated": extrapolated,
        "flags": flags,
        "assumptions": assumptions,
    }


def win_probability(pred: dict, bid: float) -> float:
    """P(win at bid B) = share of cohort comps that sold <= B (spec §8.4)."""
    if pred.get("win_basis") == "empirical" and pred.get("win_values"):
        return ecdf(pred["win_values"], bid)
    return norm_cdf((bid - pred["p50"]) / pred["win_sigma"])


def bid_for_probability(pred: dict, p: float) -> float:
    if pred.get("win_basis") == "empirical" and pred.get("win_values"):
        return percentile(pred["win_values"], p * 100)
    # invert normal by bisection
    lo, hi = pred["p50"] - 6 * pred["win_sigma"], pred["p50"] + 6 * pred["win_sigma"]
    for _ in range(60):
        mid = (lo + hi) / 2
        if win_probability(pred, mid) < p:
            lo = mid
        else:
            hi = mid
    return max(0.0, hi)


def confidence(spec: dict, cohort: dict, methods: dict, extrapolated: bool, min_n: int) -> dict:
    score = 100.0
    reasons = []
    n = methods.get("A", {}).get("n", 0)
    if n == 0:
        score -= 45
        reasons.append("No cohort comps")
    elif n < min_n:
        score -= 30
        reasons.append(f"Thin cohort (N={n} < {min_n})")
    elif n < 10:
        score -= 10
        reasons.append(f"Moderate cohort (N={n})")
    else:
        reasons.append(f"Cohort N={n}")
    if n:
        step = cohort["step"]
        score -= min(30, step * 6)
        if step:
            reasons.append(f"Cohort widened {step} step(s): {band_label(cohort)}")
    if cohort.get("scope") in ("super", "mixed_engine"):
        score -= 10
    if "B" in methods:
        reasons.append(f"Regression N={methods['B']['n']}, R²={methods['B']['r2']}")
    else:
        score -= 5
        reasons.append("No regression (insufficient family data)")
    if extrapolated:
        score -= 35
        reasons.append("Extrapolated: target is newer than any comp / no family comps")
    if spec.get("tamper_flag"):
        score -= 20
        reasons.append("ODOM vs ECM mismatch (tamper signal)")
    if "C" in methods and methods["C"]["fmv"].get("stale"):
        score -= 5
        reasons.append("Retail FMV is stale")
    score = max(0.0, min(100.0, score))
    level = "High" if score >= 70 else "Med" if score >= 45 else "Low"
    return {"level": level, "score": round(score), "reasons": reasons}


def _comp_public(c: dict) -> dict:
    keys = ("id", "sale_id", "sale_date", "location", "lot_no", "year", "make", "model_raw", "model_group",
            "engine_family", "engine_raw", "transmission", "sleeper", "miles", "hours", "hours_metric", "door_type",
            "sold_price", "source_file", "tenant_id")
    out = {k: c.get(k) for k in keys}
    if isinstance(out.get("sale_date"), date):
        out["sale_date"] = out["sale_date"].isoformat()
    out["shared"] = c.get("tenant_id") is None
    return out
