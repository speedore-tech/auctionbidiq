"""Normalization & enrichment (spec §6) — the biggest accuracy lever.

All mapping tables are plain data so they can be audited and extended.
"""
from __future__ import annotations

import re
from datetime import date

MAKE_MAP = {
    "FRTL": "FRTL", "FREIGHTLINER": "FRTL", "FRHT": "FRTL",
    "INTER": "INTL", "INTL": "INTL", "INTERNATIONAL": "INTL", "NAVISTAR": "INTL",
    "KENW": "KENW", "KENWORTH": "KENW",
    "PETE": "PETE", "PTRB": "PETE", "PETERBILT": "PETE",
    "VOLVO": "VOLVO", "VOLV": "VOLVO",
    "MACK": "MACK",
    "HINO": "HINO",
    "ISUZU": "ISUZU", "ISU": "ISUZU",
    "WSTR": "WSTR", "WESTERN": "WSTR",
    "UTIL": "UTIL", "UTILITY": "UTIL",
    "GDAN": "GDAN", "GREAT": "GDAN", "GREATDANE": "GDAN",
    "WABA": "WABASH", "WABASH": "WABASH", "WABSH": "WABASH",
    "HYUN": "HYUNDAI", "HYUNDAI": "HYUNDAI",
    "STOU": "STOUGHTON", "STOUGHTON": "STOUGHTON",
    "VANG": "VANGUARD", "VANGUARD": "VANGUARD", "CIMC": "VANGUARD",
    "WESST": "WSTR", "WESTERN STAR": "WSTR",
    "HYNDA": "HYUNDAI",
    "CHEV": "CHEVROLET",
    "MAC": "MAC",
}

# Result sheets truncate makes to 5 characters (UTILI, WABAS, VANGU, STOUG, REITN…) and sometimes
# pad with a 0 (VANG0). Catalogs spell them out. Both resolve to the same canonical make via these names.
FULL_MAKES = ["UTILITY", "WABASH", "VANGUARD", "HYUNDAI", "STOUGHTON", "REITNOUER", "WILSON", "FONTAINE",
              "DOONAN", "MANAC", "TRANSCRAFT", "FRUEHAUF", "DORSEY", "EXTREME", "KENTUCKY", "BENSON", "PITTS",
              "POLAR", "HEIL", "BRENNER", "TIMPTE", "LANDOLL", "TALBERT", "OTTAWA", "CAPACITY", "CHEVROLET",
              "TRAILMOBILE", "LUFKIN", "STRICK", "CONWAY", "COTTRELL", "MOFFETT", "TREMCAR", "TRAIL KING",
              "GREAT DANE", "WESTERN STAR", "INTERNATIONAL", "FREIGHTLINER", "KENWORTH", "PETERBILT",
              "STEPHENS", "CHEETAH", "WALKER", "LUFKIN", "ATRO", "MAC", "HOBBS", "TRAVIS", "MERCEDES"]

TRAILER_MAKES = {"UTIL", "GDAN", "WABASH", "HYUNDAI", "STOUGHTON", "VANGUARD", "KENTUCKY", "FRUEHAUF",
                 "DORSEY", "POLAR", "FONTAINE", "REITNOUER", "TRANSCRAFT", "HEIL", "WILSON", "BENSON",
                 "EXTREME", "PITTS", "BRENNER", "TREMCAR", "TRAIL", "MANAC", "TRAILMOBILE", "LANDOLL",
                 "DOONAN", "TIMPTE", "TALBERT", "LUFKIN", "STRICK", "CONWAY", "COTTRELL", "STEPHENS",
                 "CHEETAH", "WALKER", "TRAIL KING", "MAC", "HOBBS", "TRAVIS", "ATRO"}

# Result-sheet CLASS codes for trailers and the trailer type each implies.
TRAILER_CLASSES = {"VAN": "DRYVAN", "REEFR": "REEFER", "REEF": "REEFER", "FLAT": "FLATBED", "DROPD": "DROPDECK",
                   "LOWBY": "DROPDECK", "DBLDR": "DROPDECK", "MACHY": "DROPDECK", "TANKR": "TANKER",
                   "GRAIN": "DUMP_HOPPER", "ENDMP": "DUMP_HOPPER", "TRL": None}

# (make, regex on model_raw, model_group). Ordered: first match wins.
# NOTE: "M2 106" and "M2 106 BUSINESS CLAS" are the SAME group (Business Class is the
# family name); M2 106 vs M2 112 are DIFFERENT groups.
MODEL_RULES: list[tuple[str, str, str]] = [
    ("FRTL", r"\bM2\s*-?\s*112\b|\bM2112\b", "FRTL_M2_112"),
    ("FRTL", r"\bM2\s*-?\s*106\b|\bM2106\b|BUSINESS\s*CLAS|\bM2\b", "FRTL_M2_106"),
    ("FRTL", r"CASCADIA|\bCA1(25|26)\b|\bCASC\b", "FRTL_CASCADIA"),
    ("FRTL", r"COLUMBIA", "FRTL_COLUMBIA"),
    ("FRTL", r"CORONADO", "FRTL_CORONADO"),
    ("INTL", r"\bMV\s*-?\s*607\b|\bMV\b", "INTL_MV607"),
    ("INTL", r"\b4300\b|DURASTAR|\b4400\b", "INTL_4300"),
    ("INTL", r"\bLT\s*-?\s*625\b", "INTL_LT625"),
    ("INTL", r"\bLT\b|\bLT6\d\d\b", "INTL_LT"),
    ("INTL", r"PROSTAR", "INTL_PROSTAR"),
    ("INTL", r"\bRH\b|\bRH613\b", "INTL_RH"),
    ("INTL", r"\bHV\b|\bHV\d{3}\b", "INTL_HV"),
    ("VOLVO", r"\bVNL\s*\d*", "VOLVO_VNL"),
    ("VOLVO", r"\bVNR\s*\d*", "VOLVO_VNR"),
    ("KENW", r"\bT\s*-?\s*680\b", "KENW_T680"),
    ("KENW", r"\bT\s*-?\s*270\b", "KENW_T270"),
    ("KENW", r"\bT\s*-?\s*370\b", "KENW_T370"),
    ("KENW", r"\bW\s*-?\s*900[A-Z]?\b", "KENW_W900"),
    ("PETE", r"\b579\b", "PETE_579"),
    ("PETE", r"\b389\b", "PETE_389"),
    ("PETE", r"\b337\b", "PETE_337"),
    ("PETE", r"\b348\b", "PETE_348"),
    ("HINO", r"\b268\b|\bL6\b", "HINO_268"),
    ("HINO", r"\b338\b|\bL7\b", "HINO_338"),
    ("ISUZU", r"\bNPR\b", "ISUZU_NPR"),
    ("ISUZU", r"\bNRR\b|\bFTR\b", "ISUZU_NRR_FTR"),
    ("MACK", r"ANTHEM", "MACK_ANTHEM"),
    ("MACK", r"PINNACLE|\bCXU\b", "MACK_PINNACLE"),
]

# Medium-duty box super-group: direct cross-shopping alternatives (spec §6).
SUPER_GROUPS = {
    "FRTL_M2_106": "MD_BOX", "INTL_MV607": "MD_BOX", "INTL_4300": "MD_BOX",
    "HINO_268": "MD_BOX", "HINO_338": "MD_BOX", "KENW_T270": "MD_BOX", "PETE_337": "MD_BOX",
    "ISUZU_NRR_FTR": "MD_BOX",
    "FRTL_M2_112": "HD_STRAIGHT", "KENW_T370": "HD_STRAIGHT", "PETE_348": "HD_STRAIGHT", "INTL_HV": "HD_STRAIGHT",
    "INTL_LT625": "SLEEPER_TRACTOR", "INTL_LT": "SLEEPER_TRACTOR", "FRTL_CASCADIA": "SLEEPER_TRACTOR",
    "VOLVO_VNL": "SLEEPER_TRACTOR", "KENW_T680": "SLEEPER_TRACTOR", "PETE_579": "SLEEPER_TRACTOR",
    "INTL_PROSTAR": "SLEEPER_TRACTOR", "MACK_ANTHEM": "SLEEPER_TRACTOR", "FRTL_COLUMBIA": "SLEEPER_TRACTOR",
    "HYUNDAI_REEFER": "REEFER_53", "VANGUARD_REEFER": "REEFER_53",
}

# (regex on engine_raw, engine_family, desirability weight 0..1)
# Weight = serviceability / nationwide parts / resale desirability (spec §6).
ENGINE_RULES: list[tuple[str, str, float]] = [
    (r"\bX\s*-?\s*15\b|\bISX\s*15?\b", "CUMMINS_X15", 0.95),
    (r"\bX\s*-?\s*12\b", "CUMMINS_X12", 0.8),
    (r"\bISL\b|\bL\s*-?\s*9\b|8\.9", "CUMMINS_L9", 0.9),
    (r"\bISB\b|\bB\s*-?\s*6\.7\b|6\.7|\bB67\b", "CUMMINS_B67", 1.0),
    (r"\bDD\s*-?\s*5\b", "DETROIT_DD5", 0.6),
    (r"\bDD\s*-?\s*8\b", "DETROIT_DD8", 0.65),
    (r"\bDD\s*-?\s*13\b", "DETROIT_DD13", 0.85),
    (r"\bDD\s*-?\s*15\b", "DETROIT_DD15", 0.9),
    (r"\bA\s*-?\s*26\b", "NAVISTAR_A26", 0.6),
    (r"\bS13\b", "NAVISTAR_S13", 0.65),
    (r"MAXX\s*FORCE|MAXXFORCE|\bMF\s*\d", "NAVISTAR_MAXXFORCE", 0.3),
    (r"\bMX\s*-?\s*13\b", "PACCAR_MX13", 0.85),
    (r"\bMX\s*-?\s*11\b", "PACCAR_MX11", 0.75),
    (r"PACCAR\s*PX\s*-?\s*9|\bPX\s*-?\s*9\b", "PACCAR_PX9", 0.85),
    (r"PACCAR\s*PX\s*-?\s*7|\bPX\s*-?\s*7\b", "PACCAR_PX7", 0.85),
    (r"\bD\s*-?\s*13(?:TC)?\b", "VOLVO_D13", 0.8),
    (r"\bD\s*-?\s*11\b", "VOLVO_D11", 0.7),
    (r"\bMP\s*-?\s*8\b", "MACK_MP8", 0.75),
    (r"\bMP\s*-?\s*7\b", "MACK_MP7", 0.65),
    (r"\bISM\b|\bM11\b", "CUMMINS_ISM", 0.6),
    (r"\bN\s*-?\s*14\b", "CUMMINS_N14", 0.55),
    (r"CATERPILLAR|\bCAT\b|\bC1[35]\b|\b3406", "CAT_HEAVY", 0.6),
    (r"SERIES\s*60|\b12\.7\s*LITER", "DETROIT_S60", 0.6),
    (r"HINO\s*J0?8|\bJ08\b", "HINO_J08", 0.7),
    (r"\b4HK1\b", "ISUZU_4HK1", 0.7),
    (r"THERMO\s*KING|\bTK\b|PRECEDENT|\bS-?\d{3}\b", "REEFER_TK", 0.9),
    (r"CARRIER|\bX4\b|VECTOR", "REEFER_CARRIER", 0.85),
]
ENGINE_WEIGHTS = {fam: w for _, fam, w in ENGINE_RULES}

TRANS_RULES: list[tuple[str, str]] = [
    (r"ALLISON|\bA/?T\b|AUTOMATIC|\b2[05]00\b|\b3000\b", "ALLISON_AUTO"),
    (r"DT\s*-?\s*12|I\s*-?\s*SHIFT|ENDURANT|ULTRASHIFT|\bAMT\b|\bAUTO\b|\bTX\s*-?\s*12\b|MXT|\bAUTOSHIFT\b", "AMT"),
    (r"\b18\s*-?\s*SP|\b18SPD\b", "MANUAL_18SPD"),
    (r"\b13\s*-?\s*SP|\b13SPD\b", "MANUAL_13SPD"),
    (r"\b10\s*-?\s*SP|\b10SPD\b", "MANUAL_10SPD"),
    (r"\b9\s*-?\s*SP|\b9SPD\b", "MANUAL_9SPD"),
    (r"\b6\s*-?\s*SP|\b6SPD\b|MANUAL|\bM/?T\b", "MANUAL"),
]

BODY_CLASSES = ["BOX_TRUCK", "STRAIGHT_TRUCK", "TRACTOR_SLEEPER", "TRACTOR_DAYCAB", "REEFER_TRAILER", "DRY_VAN_TRAILER", "OTHER"]

USAGE_BY_CLASS = {"REEFER_TRAILER": "hours", "DRY_VAN_TRAILER": "none"}


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").upper()).strip()


def norm_make(raw: str | None) -> str | None:
    s = clean(raw)
    if not s:
        return None
    if s in MAKE_MAP:
        return MAKE_MAP[s]
    s0 = re.sub(r"0+$", "", s)                      # "VANG0" -> "VANG"
    if s0 in MAKE_MAP:
        return MAKE_MAP[s0]
    full = s
    if len(s0) >= 3 and " " not in s0:
        full = next((f for f in FULL_MAKES if f.startswith(s0)), s)   # "WABAS" -> "WABASH"
    if full in MAKE_MAP:
        return MAKE_MAP[full]
    s1 = full.split(" ")[0]
    if full in ("TRAIL KING",):
        return "TRAIL KING"
    return MAKE_MAP.get(s1, s1)


TRAILER_TYPES: list[tuple[str, str]] = [
    (r"REEF|REFRIG|MULTI-?TEMP|3000\s*R|EVEREST|ARCTIC|INNOCOOL|COOL\s*GLOBE", "REEFER"),
    (r"FLAT\s*BED|FLATBED|\bFLAT\b", "FLATBED"),
    (r"DROP\s*DECK|STEP\s*DECK|DOUBLE\s*DROP|LOWBOY|RGN", "DROPDECK"),
    (r"TANK|BARREL|GALLON", "TANKER"),
    (r"DUMP|END\s*DUMP|BELLY|HOPPER|GRAIN", "DUMP_HOPPER"),
    (r"CAR\s*(?:HAULER|CARRIER)|AUTO", "CARHAULER"),
    (r"CHASSIS|CONTAINER", "CHASSIS"),
    (r"VAN|DURAPLATE|PLATE|4000\s*D|SHEET|ALUMINUM", "DRYVAN"),
]


def trailer_type(*texts: str | None) -> str:
    for x in texts:                                  # a result-sheet class code is definitive
        k = TRAILER_CLASSES.get(clean(x))
        if k:
            return k
    t = " ".join(clean(x) for x in texts if x)
    for rx, kind in TRAILER_TYPES:
        if re.search(rx, t):
            return kind
    return "OTHER"


def is_trailer(make: str | None, *hints: str | None) -> bool:
    mk = norm_make(make)
    h = " ".join(clean(x) for x in hints if x)
    return mk in TRAILER_MAKES or bool(re.search(r"\bTRL\b|TRAILER", h)) or \
        any(clean(x) in TRAILER_CLASSES for x in hints if x)


def model_group(make: str | None, model_raw: str | None, *hints: str | None) -> str | None:
    """hints = class / description text; used to type trailers (reefer, dry van, flatbed…)."""
    mk = norm_make(make)
    m = clean(model_raw)
    if not mk and not m:
        return None
    if is_trailer(make, *hints):
        return f"{mk or 'UNK'}_{trailer_type(model_raw, *hints)}"
    for rule_make, rx, grp in MODEL_RULES:
        if mk == rule_make and re.search(rx, m):
            return grp
    first = re.sub(r"[^A-Z0-9]", "", m.split(" ")[0]) if m else "UNK"
    return f"{mk or 'UNK'}_{first or 'UNK'}"


def super_group(group: str | None) -> str | None:
    if group and group.endswith("_REEFER"):
        return "REEFER_53"
    if group and group.endswith("_DRYVAN"):
        return "DRYVAN_53"
    return SUPER_GROUPS.get(group or "")


# Super-groups the pricing engine may widen into when a model's own cohort is thin.
# MD_BOX is spec'd (M2 106 / MV607 / 4300 are direct alternatives); 53' reefers are
# near-commodity across brands. Tractors are NOT cross-shopped (brand/engine price apart).
CROSS_SHOP = {"MD_BOX", "REEFER_53", "DRYVAN_53"}


def cross_shop_group(group: str | None) -> str | None:
    sg = super_group(group)
    return sg if sg in CROSS_SHOP else None


def engine_family(raw: str | None) -> str | None:
    s = clean(raw)
    if not s:
        return None
    for rx, fam, _ in ENGINE_RULES:
        if re.search(rx, s):
            return fam
    return "OTHER:" + re.sub(r"[^A-Z0-9]+", "_", s)[:30]


def engine_weight(fam: str | None) -> float:
    if not fam:
        return 0.7  # unknown -> neutral (never demote for blank data)
    return ENGINE_WEIGHTS.get(fam, 0.7)


def transmission(raw: str | None) -> str | None:
    s = clean(raw)
    if not s:
        return None
    for rx, t in TRANS_RULES:
        if re.search(rx, s):
            return t
    return "OTHER"


def body_class(make: str | None, group: str | None, class_raw: str | None, desc_raw: str | None) -> str:
    mk = norm_make(make)
    cls = clean(class_raw)
    desc = clean(desc_raw)
    sg = super_group(group)
    if is_trailer(make, class_raw):
        kind = trailer_type(class_raw, group, desc_raw)
        return {"REEFER": "REEFER_TRAILER", "DRYVAN": "DRY_VAN_TRAILER"}.get(kind, "OTHER")
    if re.search(r"SLP|SLEEP|RAISED|MID\s*ROOF|HI\s*RISE|\d{2,3}\s*(IN|\")", desc) or sg == "SLEEPER_TRACTOR":
        if re.search(r"DAY", desc):
            return "TRACTOR_DAYCAB"
        return "TRACTOR_SLEEPER"
    # T&M result sheets class every tractor "CONV" (or CABOV); no sleeper text -> day cab
    if re.search(r"DAY\s*CAB|\bDAY\b", desc) or re.search(r"TRAC|TRACTOR|^CONV$|^CABOV$", cls):
        return "TRACTOR_DAYCAB"
    if sg == "MD_BOX" or re.search(r"BOX|VAN\s*BODY|REEFER\s*BODY", desc):
        return "BOX_TRUCK"
    if sg == "HD_STRAIGHT" or re.search(r"TRUCK|STRT|STRAIGHT|DUMP|FLAT", cls + " " + desc):
        return "STRAIGHT_TRUCK"
    return "OTHER"


def sleeper(desc_raw: str | None) -> str | None:
    d = clean(desc_raw)
    m = re.search(r"(\d{2,3})\s*(?:IN|\"|'')?\s*(?:RAISED|MID|FLAT|HI)?\s*(?:ROOF)?\s*(?:SLP|SLEEP)", d)
    if m:
        return f"{m.group(1)}IN"
    if re.search(r"RAISED", d):
        return "RAISED_ROOF"
    if re.search(r"SLP|SLEEP", d):
        return "SLEEPER"
    return None


def enrich(rec: dict) -> dict:
    """Fill normalized + computed fields on a dict of raw spec fields (in place)."""
    rec["make"] = norm_make(rec.get("make")) or rec.get("make")
    if not rec.get("model_group"):
        rec["model_group"] = model_group(rec.get("make"), rec.get("model_raw"), rec.get("class_raw"),
                                         rec.get("desc_raw"), rec.get("body_class") if rec.get("body_class") in
                                         ("REEFER_TRAILER", "DRY_VAN_TRAILER") else None)
    rec["super_group"] = super_group(rec.get("model_group"))
    if rec.get("engine_raw") and not rec.get("engine_family"):
        rec["engine_family"] = engine_family(rec["engine_raw"])
    if rec.get("trans_raw") and not rec.get("transmission"):
        rec["transmission"] = transmission(rec["trans_raw"])
    if not rec.get("body_class"):
        rec["body_class"] = body_class(rec.get("make"), rec.get("model_group"), rec.get("class_raw"), rec.get("desc_raw"))
    if not rec.get("sleeper") and rec.get("desc_raw"):
        rec["sleeper"] = sleeper(rec["desc_raw"])
    yr = rec.get("year")
    sd = rec.get("sale_date")
    sale_year = sd.year if isinstance(sd, date) else date.today().year
    if yr:
        # Model years run ~1 yr ahead of calendar; floor age at 0.5 to keep mpy sane.
        age = max(0.5, sale_year - int(yr) + 0.5)
        rec["age_years"] = round(age, 2)
        if rec.get("miles") is not None:
            rec["miles_per_year"] = round(rec["miles"] / age, 0)
    hrs = rec.get("hours")
    if hrs and rec.get("miles") and rec.get("body_class") not in ("REEFER_TRAILER", "DRY_VAN_TRAILER"):
        rec["lifetime_mph"] = round(rec["miles"] / hrs, 1)
    return rec


def normalize_year(y: str | int | None) -> int | None:
    if y is None or y == "":
        return None
    try:
        v = int(str(y).strip())
    except ValueError:
        return None
    if v < 100:
        v += 2000 if v <= (date.today().year % 100) + 1 else 1900
    return v if 1950 <= v <= date.today().year + 2 else None
