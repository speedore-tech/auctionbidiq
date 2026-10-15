"""CSV readers for catalogs and mileage / reefer-hours sheets (spec §5.2, §5.3).
The real T&M PDF formats are parsed in tm_formats.py.
"""
from __future__ import annotations

import csv
import io
import re

from ..normalize import normalize_year
from .tm_formats import norm_lot



def _num(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _bool(s) -> bool | None:
    if s is None or str(s).strip() == "":
        return None
    return str(s).strip().upper() in ("Y", "YES", "TRUE", "1", "X")


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


# ---------------------------------------------------------------- tables / CSV
def read_csv_rows(data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    return [{_norm_header(k): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(io.StringIO(text))]


# ---------------------------------------------------------------- mileage/hours
def _pick(row: dict, *keys) -> str | None:
    for k in keys:
        for rk, v in row.items():
            if (rk == k or rk.startswith(k)) and v not in (None, ""):
                return v
    return None


def parse_mileage_rows(rows: list[dict], tolerance_pct: float = 10.0) -> list[dict]:
    """Map raw rows -> {lot_no, odometer_miles, ecm_miles, reefer_*_hours, tamper_flag}."""
    out = []
    for r in rows:
        lot = _pick(r, "lot_no", "lot", "lot_number")
        if not lot:
            continue
        lot = norm_lot(lot)
        odo = _num(_pick(r, "odometer", "odom", "odo"))
        odo_in_k = any(("000" in k or k.endswith("_k") or "thous" in k) for k in r if k.startswith("odo"))
        if odo is not None and (odo_in_k or odo < 3000):
            odo *= 1000  # sheets list odometer in thousands
        ecm = _num(_pick(r, "ecm_miles", "ecm"))
        total_h = _num(_pick(r, "total_hours", "total_hrs", "total"))
        eng_h = _num(_pick(r, "engine_hours", "engine_hrs", "eng_hours", "eng_hrs", "engine"))
        elec_h = _num(_pick(r, "electric_hours", "electric_hrs", "elec_hours", "elec_hrs", "electric", "elec"))
        tamper = False
        if odo and ecm:
            diff = abs(odo - ecm) / max(odo, ecm) * 100
            tamper = diff > tolerance_pct
        out.append({
            "lot_no": lot,
            "odometer_miles": int(odo) if odo is not None else None,
            "ecm_miles": int(ecm) if ecm is not None else None,
            "reefer_total_hours": total_h,
            "reefer_engine_hours": eng_h,
            "reefer_electric_hours": elec_h,
            "tamper_flag": tamper,
        })
    return out


# ---------------------------------------------------------------- catalogs
CATALOG_FIELDS = {
    "lot_no": ("lot_no", "lot", "lot_number"),
    "year": ("year", "yr", "model_year"),
    "make": ("make",),
    "model_raw": ("model", "model_raw"),
    "class_raw": ("class", "class_raw", "type"),
    "engine_raw": ("engine", "engine_raw"),
    "desc_raw": ("description", "desc", "body", "sleeper_body"),
    "trans_raw": ("transmission", "trans"),
    "miles": ("miles", "mileage", "odometer"),
    "hours": ("hours", "reefer_hours"),
    "hours_metric": ("hours_metric",),
    "vin": ("vin",),
    "gvwr": ("gvwr", "gvw", "gvwr_lbs"),
    "front_axle_lbs": ("front_axle", "front_axle_lbs", "fa"),
    "rear_axle_lbs": ("rear_axle", "rear_axle_lbs", "ra"),
    "body_length_ft": ("body_length", "body_length_ft", "length", "length_ft"),
    "apu": ("apu",),
    "sleeper_size_in": ("sleeper_size", "sleeper_size_in", "sleeper_in"),
    "liftgate": ("liftgate", "lift_gate"),
    "door_type": ("door", "door_type"),
    "engine_brake": ("engine_brake", "jake"),
    "condition_notes": ("notes", "condition", "condition_notes"),
    "branded_title": ("branded", "branded_title"),
    "non_operable": ("non_op", "non_operable", "nonop"),
    "body_class": ("body_class",),
}
INT_FIELDS = {"miles", "gvwr", "front_axle_lbs", "rear_axle_lbs", "sleeper_size_in"}
FLOAT_FIELDS = {"hours", "body_length_ft"}
BOOL_FIELDS = {"apu", "liftgate", "engine_brake", "branded_title", "non_operable"}


def catalog_from_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        rec: dict = {}
        for field, keys in CATALOG_FIELDS.items():
            v = None
            for k in keys:
                if r.get(k) not in (None, ""):
                    v = r[k]
                    break
            if v is None:
                continue
            if field in INT_FIELDS:
                n = _num(v)
                v = int(n) if n is not None else None
            elif field in FLOAT_FIELDS:
                v = _num(v)
            elif field in BOOL_FIELDS:
                v = _bool(v)
            elif field == "year":
                v = normalize_year(v)
            if v is not None:
                rec[field] = v
        if rec.get("lot_no"):
            rec["lot_no"] = norm_lot(rec["lot_no"])
            out.append(rec)
    return out




