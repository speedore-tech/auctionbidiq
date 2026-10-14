from datetime import date

from bidiq.fmv import summarize_fmv, validate_source
from bidiq.normalize import enrich
from bidiq.pricing.engine import fit_regression, predict, win_probability
from bidiq.recommender import all_in, hammer_from_all_in, plan_multi_lot, recommend
from bidiq.risk import cdl_check, vin_check, warranty_check

from .conftest import make_comps

SD = date(2026, 5, 1)


def spec(**kw):
    base = {"make": "INTER", "model_raw": "LT625", "engine_raw": "NAVISTAR A26", "class_raw": "CONV",
            "desc_raw": "73IN RAISED SLP", "year": 2020, "miles": 450_000, "sale_date": SD}
    base.update(kw)
    return enrich(base)


def test_regression_recovers_slopes(settings):
    comps = make_comps(n=120, noise=0.02)
    reg = fit_regression(spec(), comps)
    assert reg and "usage" in reg["features"]
    assert abs(reg["dollars_per_100k_miles"] - 2585) < 500
    assert abs(reg["dollars_per_year"] - 5461) < 900


def test_mileage_moves_the_number(settings):
    """416k-mi and 1M-mi units must not both price at the same number."""
    comps = make_comps(n=120)
    lo = predict(spec(miles=416_000), comps, settings)
    hi = predict(spec(miles=1_000_000), comps, settings)
    assert lo["ok"] and hi["ok"]
    assert lo["p50"] - hi["p50"] > 10_000


def test_excludes_nonop_and_branded(settings):
    comps = make_comps(n=40)
    junk = [dict(c, id=1000 + i, non_operable=True, sold_price=1000) for i, c in enumerate(comps[:10])]
    p1 = predict(spec(), comps, settings)
    p2 = predict(spec(), comps + junk, settings)
    assert p1["p50"] == p2["p50"]


def test_tractor_cohort_separates_engines(settings):
    a26 = make_comps(n=40, seed=2)
    x15 = [dict(enrich({**c, "engine_raw": "CUMMINS X15", "engine_family": None}), id=500 + i,
                sold_price=c["sold_price"] + 8000) for i, c in enumerate(make_comps(n=40, seed=3))]
    p = predict(spec(), a26 + x15, settings)
    assert all(c["engine_family"] == "NAVISTAR_A26" for c in p["comps"])


def test_newer_than_any_comp_uses_retail_minus_low_confidence(settings):
    comps = make_comps(n=60, years=range(2017, 2021))
    fmv = {"value": 100_000, "tier": "private", "as_of": "2026-04-01", "stale": False}
    p = predict(spec(year=2023, miles=150_000), comps, settings, fmv)
    assert p["extrapolated"] and "NEWER_THAN_ANY_COMP" in p["flags"]
    assert p["weights"]["C"] > 0.5
    assert p["confidence"]["level"] == "Low"


def test_no_data_is_an_explicit_error(settings):
    p = predict(spec(make="MACK", model_raw="ANTHEM"), make_comps(n=20), settings)
    assert not p["ok"] and "No comps" in p["error"]


def test_all_in_round_trip():
    assert all_in(40_000, 5, 1_200, 150) == 40_000 * 1.05 + 1_350
    assert abs(hammer_from_all_in(all_in(40_000, 10, 900, 150), 10, 900, 150) - 40_000) < 1e-6


def test_recommend_thresholds_and_blind_reserve(settings):
    comps = make_comps(n=80)
    p = predict(spec(), comps, settings)
    lot = {"body_class": "TRACTOR_SLEEPER", "transport_miles": 500}
    r = recommend(p, lot, settings, None, None, {})
    h = r["hammer"]
    assert h["best_buy"] <= h["target"] <= h["walk_away"] <= h["hard_ceiling"]
    assert h["hard_ceiling"] <= p["max"]
    assert r["cost_basis"]["transport"]["cost"] == 500 * settings["driveaway_rate_per_mile"]
    blind = recommend(p, {**lot, "blind_buy": True}, settings, None, None, {})
    assert blind["reserve"]["total"] == 7500
    assert blind["hammer"]["hard_ceiling"] <= h["hard_ceiling"] - 7400
    # premium per sale beats tenant default
    sale = {"sale_id": "X", "buyers_premium_pct": 10.0}
    r10 = recommend(p, lot, settings, sale, None, {})
    assert r10["cost_basis"]["buyers_premium_pct"] == 10.0 and r10["all_in"]["target"] > r["all_in"]["target"]
    # empirical win prob = share of cohort sold <= bid
    assert win_probability(p, 0) == 0 and win_probability(p, 10**7) == 1


def test_budget_cap_limits_ceiling(settings):
    p = predict(spec(), make_comps(n=80), settings)
    r = recommend(p, {"transport_miles": 0}, settings, None, None, {"budget_cap_all_in": 20_000})
    assert r["all_in"]["hard_ceiling"] <= 20_000


def test_multi_lot_plan():
    items = [{"id": i, "lot_no": str(100 + i), "run_order": i, "premium_pct": 5, "transport": 1000, "fees": 150,
              "target_all_in": 40_000, "ceiling_all_in": 46_000} for i in range(4)]
    plan = plan_multi_lot(items, 100_000, max_wins=2, deescalate_pct=5)
    assert [p["action"] for p in plan["plan"]] == ["BID"] * 4
    live = plan_multi_lot(items, 100_000, 2, 5, {"0": {"won": True, "hammer": 40_000}, "1": {"won": True, "hammer": 41_000}})
    assert [p["action"] for p in live["plan"]] == ["WON", "WON", "SKIP", "SKIP"]
    one = plan_multi_lot(items, 100_000, 3, 5, {"0": {"won": True, "hammer": 42_000}})
    assert one["plan"][1]["max_all_in"] < 46_000  # de-escalated after a win


def test_fmv_summary_and_source_rules(settings):
    rows = [
        {"model_group": "FRTL_M2_106", "year_lo": 2018, "year_hi": 2020, "miles_lo": 100_000, "miles_hi": 250_000,
         "tier": "private", "price": 60_000, "is_asking": True, "as_of_date": date(2026, 4, 1)},
        {"model_group": "FRTL_M2_106", "year_lo": 2018, "year_hi": 2020, "miles_lo": 100_000, "miles_hi": 250_000,
         "tier": "private", "price": 70_000, "is_asking": True, "as_of_date": date(2026, 4, 5)},
    ]
    s = summarize_fmv(rows, {"model_group": "FRTL_M2_106", "year": 2019, "miles": 190_000}, settings, date(2026, 5, 1))
    assert s["value"] == 58_500  # median 65k asking, -10% to transaction
    assert not s["stale"]
    stale = summarize_fmv(rows, {"model_group": "FRTL_M2_106", "year": 2019}, settings, date(2026, 9, 1))
    assert stale["stale"]
    assert validate_source("private", "Taylor & Martin auction") is not None
    assert validate_source("private", "TruckPaper") is None


def test_vin_cdl_warranty(settings):
    ok = vin_check("1M8GDM9AXKP042788")
    assert ok["check_digit_ok"]
    bad = vin_check("1M8GDM9A1KP042788")
    assert not bad["check_digit_ok"] and bad["issues"]
    assert cdl_check(None, 17_500)["cdl_required"] is False
    assert cdl_check(None, 19_000)["status"] == "borderline"
    assert cdl_check(None, 21_000)["cdl_required"] is True
    assert cdl_check(33_000, None)["cdl_required"] is True
    w = warranty_check(2021, 200_000, 25_999, "BOX_TRUCK", settings, date(2026, 5, 1))
    assert w["eligible"]
    assert not warranty_check(2021, 400_000, 25_999, "BOX_TRUCK", settings, date(2026, 5, 1))["eligible"]
    assert not warranty_check(2015, 100_000, 25_999, "BOX_TRUCK", settings, date(2026, 5, 1))["eligible"]
