"""App configuration and per-tenant default settings.

Every dollar figure below that is not sourced from sold comps is an *assumption*
and is surfaced as such in the UI (Settings page) so the buyer can change it.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
def _database_url() -> str:
    """BIDIQ_DATABASE_URL wins; else build a Postgres URL from parts (password URL-encoded,
    so @ : / # % in it can't break the URL); else local SQLite."""
    if os.environ.get("BIDIQ_DATABASE_URL"):
        return os.environ["BIDIQ_DATABASE_URL"]
    host = os.environ.get("BIDIQ_DB_HOST")
    if host:
        from urllib.parse import quote

        user = quote(os.environ.get("BIDIQ_DB_USER", "auctionbidiq"), safe="")
        pw = quote(os.environ.get("BIDIQ_DB_PASSWORD") or os.environ.get("DB_PASSWORD", ""), safe="")
        port = os.environ.get("BIDIQ_DB_PORT", "5432")
        name = os.environ.get("BIDIQ_DB_NAME", "auctionbidiq")
        return f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{name}"
    return f"sqlite:///{BASE_DIR / 'bidiq.db'}"


DATABASE_URL = _database_url()
UPLOAD_DIR = Path(os.environ.get("BIDIQ_UPLOAD_DIR", BASE_DIR / "uploads"))
TOKEN_TTL_DAYS = int(os.environ.get("BIDIQ_TOKEN_TTL_DAYS", "30"))
# Public self-service sign-up. Turn off in production; create accounts with scripts/create_admin.py.
ALLOW_REGISTRATION = os.environ.get("BIDIQ_ALLOW_REGISTRATION", "true").lower() in ("1", "true", "yes")

DEFAULT_SETTINGS: dict = {
    # --- home / transport (spec §8.2) ---
    "home_label": "Dallas-Fort Worth, TX",
    "home_lat": 32.8998,
    "home_lon": -97.0403,
    "transport_mode": "driveaway",          # driveaway | haul
    "driveaway_rate_per_mile": 1.75,
    "haul_rate_per_mile": 2.75,
    "road_factor": 1.2,                      # great-circle -> road miles
    "transport_min_charge": 300.0,
    "title_doc_fees": 150.0,
    # --- premium (spec §8.2: never hardcode; per sale/lot overrides win) ---
    "default_buyers_premium_pct": 5.0,
    # --- recommender (spec §8.1) ---
    "aggressiveness": 0.5,                   # 0 = bid at p25, 1 = bid at p50
    "walk_buffer_pct": None,                 # None -> walk = p75; else walk = p50*(1+x%)
    "budget_cap_all_in": None,               # all-in $ cap per lot, converted to hammer
    "min_cohort_n": 5,
    # --- retail-minus fallback (spec §7.3) ---
    "auction_discount_factor": 0.62,
    "auction_discount_low": 0.55,
    "auction_discount_high": 0.70,
    # --- FMV (spec §9) ---
    "fmv_tier": "private",                   # dealer | private | wholesale
    "fmv_stale_days": 45,
    "asking_discount_pct": 10.0,
    # --- blind-buy reserve (spec §8.3) — itemized, editable assumptions ---
    "risk_reserves": {
        "dpf_def": 3500.0,
        "transmission": 4000.0,
        "liftgate": 2500.0,
        "reefer_unit": 5000.0,
        "tires_brakes": 1500.0,
    },
    # --- risk (spec §10) ---
    "reefer_max_hours": 12000,
    "odom_ecm_tolerance_pct": 10.0,
    "warranty_max_age_years": 7,
    "warranty_class2_7_mile_cap": 370000,
    "warranty_class8_mile_cap": None,        # unknown — confirm with auction
    # --- multi-lot (spec §8.5) ---
    "multi_lot_max_wins": 1,
    "multi_lot_deescalate_pct": 5.0,
    # --- comp store (spec §16 #2) ---
    "include_private_comps": True,
    "contribute_to_shared": True,
    # --- deposit/logistics defaults (spec §11.5) ---
    "default_deposit_amount": 5000.0,
    "default_wire_deadline": "3:00 PM the business day before the sale (first-time buyers)",
    "default_payment_window_hours": 24,
}


def merged_settings(overrides: dict | None) -> dict:
    s = copy.deepcopy(DEFAULT_SETTINGS)
    for k, v in (overrides or {}).items():
        if k == "risk_reserves" and isinstance(v, dict):
            s["risk_reserves"].update(v)
        elif k in s:
            s[k] = v
    return s
