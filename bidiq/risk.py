"""Risk & eligibility module (spec §10)."""
from __future__ import annotations

from datetime import date

# ------------------------------------------------------------------ CDL / GVWR
GVWR_CLASSES = [(6000, 1), (10000, 2), (14000, 3), (16000, 4), (19500, 5), (26000, 6), (33000, 7)]


def vehicle_class(gvwr: int | None) -> int | None:
    if not gvwr:
        return None
    for limit, cls in GVWR_CLASSES:
        if gvwr <= limit:
            return cls
    return 8


def cdl_check(gvwr: int | None, rear_axle_lbs: int | None, body_class: str | None = None,
              gvwr_confirmed: bool = False) -> dict:
    """Rear axle rating -> likely class; door-sticker GVWR is definitive."""
    if body_class in ("REEFER_TRAILER", "DRY_VAN_TRAILER"):
        return {"status": "n/a", "cdl_required": None, "vehicle_class": None, "message": "Trailer — CDL depends on the power unit."}
    if body_class in ("TRACTOR_SLEEPER", "TRACTOR_DAYCAB"):
        return {"status": "definitive", "cdl_required": True, "vehicle_class": 8, "message": "Class 8 tractor — CDL-A required."}
    if gvwr:
        cls = vehicle_class(gvwr)
        req = gvwr > 26000
        return {
            "status": "definitive" if gvwr_confirmed else "listed",
            "cdl_required": req,
            "vehicle_class": cls,
            "message": f"GVWR {gvwr:,} lbs → Class {cls}, {'CDL required' if req else 'non-CDL'}"
                       + ("" if gvwr_confirmed else " — confirm on door sticker (GVWR-LBS line)."),
        }
    if rear_axle_lbs:
        if rear_axle_lbs < 19000:
            return {"status": "likely", "cdl_required": False, "vehicle_class": 6,
                    "message": f"Rear axle {rear_axle_lbs:,} → likely ≤26,000 GVWR non-CDL. Confirm door-sticker GVWR."}
        if rear_axle_lbs == 19000 or rear_axle_lbs < 21000:
            return {"status": "borderline", "cdl_required": None, "vehicle_class": None,
                    "message": f"Rear axle {rear_axle_lbs:,} is borderline — GVWR confirmation REQUIRED before bidding."}
        return {"status": "likely", "cdl_required": True, "vehicle_class": 7,
                "message": f"Rear axle {rear_axle_lbs:,} → likely >26,000 GVWR, CDL required."}
    return {"status": "unknown", "cdl_required": None, "vehicle_class": None,
            "message": "No GVWR or axle ratings — get door-sticker GVWR before bidding."}


# ------------------------------------------------------------------ VIN
_TRANSLIT = {**{str(d): d for d in range(10)},
             "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
             "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
             "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"  # 30-year cycle starting 1980 / 2010

WMI = {
    "1FV": "FRTL", "1FU": "FRTL", "3AK": "FRTL", "3AL": "FRTL", "4UZ": "FRTL", "3AW": "FRTL",
    "1HT": "INTL", "1HS": "INTL", "3HA": "INTL", "3HS": "INTL", "3HT": "INTL", "3HC": "INTL", "1HA": "INTL",
    "4V4": "VOLVO", "4V5": "VOLVO", "4VG": "VOLVO",
    "1XK": "KENW", "2XK": "KENW", "3BK": "KENW", "1NK": "KENW",
    "1XP": "PETE", "2NP": "PETE", "1NP": "PETE", "3BP": "PETE",
    "1M1": "MACK", "1M2": "MACK",
    "JHH": "HINO", "JHB": "HINO", "5PV": "HINO", "2AY": "HINO",
    "JAL": "ISUZU", "54D": "ISUZU", "JAA": "ISUZU",
    "1UY": "UTIL", "1GR": "GDAN", "1JJ": "WABASH", "3H3": "HYUNDAI", "527": "VANGUARD", "1DW": "STOUGHTON",
}
COUNTRY = {"1": "USA", "4": "USA", "5": "USA", "2": "Canada", "3": "Mexico", "J": "Japan", "K": "Korea",
           "W": "Germany", "S": "UK", "V": "France/Spain", "Y": "Sweden/Finland", "L": "China"}

NICB_URL = "https://www.nicb.org/vincheck"
NMVTIS_URL = "https://vehiclehistory.bja.ojp.gov/nmvtis_vehiclehistory"


def vin_check_digit(vin: str) -> str | None:
    try:
        total = sum(_TRANSLIT[ch] * w for ch, w in zip(vin, _WEIGHTS))
    except KeyError:
        return None
    r = total % 11
    return "X" if r == 10 else str(r)


def decode_year(code: str, hint_year: int | None = None) -> int | None:
    if code not in _YEAR_CODES:
        return None
    i = _YEAR_CODES.index(code)
    candidates = [1980 + i, 2010 + i, 2040 + i]
    max_ok = date.today().year + 1
    candidates = [c for c in candidates if c <= max_ok]
    if not candidates:
        return None
    if hint_year:
        return min(candidates, key=lambda c: abs(c - hint_year))
    return max(candidates)


def vin_check(vin: str | None, year: int | None = None, make: str | None = None) -> dict:
    if not vin:
        return {"status": "missing", "valid": None, "issues": ["No VIN — get it from the catalog or door sticker."],
                "links": {"nicb": NICB_URL, "nmvtis": NMVTIS_URL}}
    v = vin.strip().upper()
    issues = []
    if len(v) != 17:
        issues.append(f"VIN length {len(v)} ≠ 17")
    if any(ch in "IOQ" for ch in v):
        issues.append("VIN contains I/O/Q (never valid) — likely mistyped")
    check = vin_check_digit(v) if len(v) == 17 else None
    check_ok = check is not None and v[8] == check
    if len(v) == 17 and not check_ok:
        issues.append(f"Check digit mismatch (position 9 is '{v[8]}', computed '{check}') — mistyped or cloned VIN")
    wmi = v[:3]
    dec_make = WMI.get(wmi)
    dec_year = decode_year(v[9], year) if len(v) == 17 else None
    if year and dec_year and dec_year != year:
        issues.append(f"VIN year {dec_year} ≠ listed year {year}")
    if make and dec_make and dec_make != make:
        issues.append(f"VIN manufacturer {dec_make} ≠ listed make {make}")
    return {
        "status": "ok" if not issues else "problem",
        "valid": check_ok and len(v) == 17,
        "vin": v,
        "check_digit_ok": check_ok,
        "decoded": {"wmi": wmi, "make": dec_make, "country": COUNTRY.get(v[0]), "year": dec_year,
                    "plant": v[10] if len(v) == 17 else None, "serial": v[11:] if len(v) == 17 else None},
        "issues": issues,
        "links": {"nicb": NICB_URL, "nmvtis": NMVTIS_URL},
        "reminder": "Cross-check decoded year/make against the door sticker. Run NICB VINCheck (stolen/total-loss) and NMVTIS (title brands).",
    }


# ------------------------------------------------------------------ Warranty
def warranty_check(year: int | None, miles: int | None, gvwr: int | None, body_class: str | None,
                   settings: dict, sale_date: date | None = None) -> dict:
    """T&M Total Trust / National Truck Protection — engine-only coverage eligibility."""
    terms = "Engine-only, 60 days / 20,000 mi, $750 deductible, $10,000 cap."
    not_covered = "Engine covered; DPF/DEF, transmission, liftgate and reefer unit are NOT covered."
    reasons = []
    if body_class in ("REEFER_TRAILER", "DRY_VAN_TRAILER"):
        return {"eligible": False, "status": "n/a", "terms": terms, "reasons": ["Trailers are not covered."], "not_covered": not_covered}
    yr = (sale_date or date.today()).year
    max_age = int(settings.get("warranty_max_age_years", 7))
    eligible = True
    unknown = False
    if not year:
        unknown = True
        reasons.append("Year unknown")
    elif yr - year > max_age:
        eligible = False
        reasons.append(f"{yr - year} model years old > {max_age}-yr limit")
    cls = vehicle_class(gvwr) or (8 if body_class in ("TRACTOR_SLEEPER", "TRACTOR_DAYCAB") else None)
    if cls is None:
        unknown = True
        reasons.append("Class unknown (need GVWR) — assumed Class 2–7 mileage cap")
    elif cls < 2:
        eligible = False
        reasons.append("Class 1 not covered (Class 2–8 only)")
    cap = settings.get("warranty_class8_mile_cap") if cls == 8 else settings.get("warranty_class2_7_mile_cap", 370000)
    if miles is None:
        unknown = True
        reasons.append("Miles unknown")
    elif cap is not None and miles > cap:
        eligible = False
        reasons.append(f"{miles:,} mi exceeds {cap:,} mi cap")
    elif cap is None:
        reasons.append("Class 8 mileage cap not configured — confirm with auction")
    status = "eligible" if eligible and not unknown else ("not eligible" if not eligible else "probably eligible")
    return {"eligible": eligible and not unknown, "status": status, "terms": terms, "reasons": reasons,
            "not_covered": not_covered, "vehicle_class": cls}


# ------------------------------------------------------------------ Reefer
def reefer_check(lot: dict, settings: dict) -> dict:
    notes, flags = [], []
    if lot.get("body_length_ft") and lot["body_length_ft"] != 53:
        notes.append(f"{lot['body_length_ft']:.0f} ft (53 ft is the liquid market)")
    if lot.get("door_type"):
        notes.append(f"Door: {lot['door_type']}")
    else:
        flags.append("DOOR_TYPE_UNKNOWN")
    max_h = settings.get("reefer_max_hours", 12000)
    if lot.get("hours") is not None:
        metric = lot.get("hours_metric") or "unknown metric"
        if lot["hours"] > max_h:
            flags.append("REEFER_HOURS_HIGH")
        notes.append(f"{lot['hours']:,.0f} hrs ({metric})")
        if metric != "engine":
            notes.append("Caution: TOTAL hours ≠ engine-only hours — don't compare across metrics.")
    else:
        flags.append("REEFER_HOURS_UNKNOWN")
    unit = settings.get("risk_reserves", {}).get("reefer_unit")
    if unit:
        notes.append(f"Secondary-market TK/Carrier unit reference: ~${unit:,.0f} (assumption, editable)")
    return {"notes": notes, "flags": flags}


# ------------------------------------------------------------------ Flags
def risk_flags(lot: dict, settings: dict, sale_absolute: bool = True) -> list[dict]:
    """Prominent flags; absolute sales have no arbitration so these are not optional."""
    f = []

    def add(code, severity, msg):
        f.append({"code": code, "severity": severity, "message": msg})

    if sale_absolute:
        add("ABSOLUTE_SALE", "info", "Absolute / as-is / no arbitration — surprises are unrecoverable.")
    if lot.get("branded_title"):
        add("BRANDED_TITLE", "high", "Branded title — different market; excluded from comp stats.")
    if lot.get("non_operable") or str(lot.get("lot_type") or "").upper() == "P":
        add("NON_OPERABLE", "high", "Non-operable / parts lot.")
    if lot.get("tamper_flag"):
        add("ODOM_ECM_MISMATCH", "high", "Odometer vs ECM miles disagree — possible tamper.")
    if lot.get("blind_buy"):
        add("BLIND_BUY", "medium", "No in-person inspection — itemized condition reserve deducted from ceiling.")
    ck = lot.get("checklist") or {}
    if lot.get("vin") and not (ck.get("nicb") and ck.get("nmvtis")):
        add("VIN_CHECKS_PENDING", "medium", "Run NICB VINCheck and NMVTIS before bidding.")
    if not lot.get("vin"):
        add("NO_VIN", "medium", "No VIN on file.")
    return f
