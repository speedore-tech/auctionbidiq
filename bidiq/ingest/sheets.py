"""Catalog (spec §5.2) and mileage / reefer-hours sheet (spec §5.3) parsers.

Catalogs: CSV (exact) or PDF (heuristic regex extraction — every field it pulls is
shown to the user for confirmation before bidding; nothing is silently inferred).
Mileage/hours sheets: CSV or PDF tables with a header row.
"""
from __future__ import annotations

import csv
import io
import re

from ..normalize import ENGINE_RULES, normalize_year

VIN_RX = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")


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


def read_pdf_table_rows(path_or_file) -> list[dict]:
    """Read all tables in a PDF; the first row that contains 'lot' is the header."""
    import pdfplumber

    out: list[dict] = []
    header: list[str] | None = None
    with pdfplumber.open(path_or_file) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    cells = [(c or "").strip() for c in row]
                    if any(re.search(r"\blot\b", c, re.I) for c in cells):
                        header = [_norm_header(c) for c in cells]
                        continue
                    if header and any(cells):
                        out.append(dict(zip(header, cells)))
    return out


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
        lot = re.sub(r"[^A-Z0-9]", "", lot.upper())
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


def apply_mileage(obj, m: dict) -> None:
    """Apply one parsed mileage row to a SoldComp/TargetLot ORM object."""
    for k in ("odometer_miles", "ecm_miles", "reefer_total_hours", "reefer_engine_hours", "reefer_electric_hours"):
        if m.get(k) is not None:
            setattr(obj, k, m[k])
    obj.tamper_flag = bool(m.get("tamper_flag"))
    if obj.miles is None:
        cands = [v for v in (m.get("odometer_miles"), m.get("ecm_miles")) if v]
        if cands:
            obj.miles = max(cands)  # conservative: assume the higher reading
    # Engine-only hours and Total hours are NOT comparable — record which one we use.
    if m.get("reefer_engine_hours") is not None:
        obj.hours, obj.hours_metric = m["reefer_engine_hours"], "engine"
    elif m.get("reefer_total_hours") is not None:
        obj.hours, obj.hours_metric = m["reefer_total_hours"], "total"


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
            rec["lot_no"] = re.sub(r"[^A-Z0-9]", "", str(rec["lot_no"]).upper())
            out.append(rec)
    return out


LOT_START_RX = re.compile(
    r"^\s*(?:LOT\s*#?\s*)?(\d{1,4}[A-Z]?)\s+((?:19|20)\d{2})\s+([A-Z]{2,12})\s+(.+)$", re.M
)


def catalog_from_text(text: str) -> list[dict]:
    """Heuristic extraction from catalog prose. Returns partial TargetLot dicts."""
    text = text.upper()
    starts = list(LOT_START_RX.finditer(text))
    out = []
    for i, m in enumerate(starts):
        block = text[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        rec: dict = {
            "lot_no": m.group(1),
            "year": normalize_year(m.group(2)),
            "make": m.group(3),
            "model_raw": m.group(4).split("  ")[0].strip()[:60],
            "condition_notes": " ".join(block.split())[:1000],
        }
        if v := VIN_RX.search(block):
            rec["vin"] = v.group(1)
        if g := re.search(r"GVWR?(?:\s*-?\s*LBS)?[:\s#]*([\d,]{5,6})", block):
            rec["gvwr"] = int(g.group(1).replace(",", ""))
        if ra := re.search(r"(?:REAR(?:\s*AXLE)?|\bRA\b|\bR/A\b)[^\d\n]{0,12}([\d,]{5,6})", block):
            rec["rear_axle_lbs"] = int(ra.group(1).replace(",", ""))
        if fa := re.search(r"(?:FRONT(?:\s*AXLE)?|\bFA\b|\bF/A\b)[^\d\n]{0,12}([\d,]{5,6})", block):
            rec["front_axle_lbs"] = int(fa.group(1).replace(",", ""))
        if ln := re.search(r"\b(\d{2})\s*(?:'|FT\b|FOOT)", block):
            rec["body_length_ft"] = float(ln.group(1))
        if mi := re.search(r"([\d,]{4,9})\s*(?:MILES|MI\b|ECM)", block):
            rec["miles"] = int(mi.group(1).replace(",", ""))
        if sl := re.search(r"(\d{2,3})\s*(?:\"|IN\b|INCH)[^\n]{0,20}SLEEP", block):
            rec["sleeper_size_in"] = int(sl.group(1))
        if re.search(r"\bAPU\b", block):
            rec["apu"] = True
        if re.search(r"LIFT\s*-?\s*GATE|\bLIFTGATE\b|\bL/G\b", block):
            rec["liftgate"] = True
        if re.search(r"JAKE|ENGINE\s*BRAKE|EXHAUST\s*BRAKE", block):
            rec["engine_brake"] = True
        if re.search(r"SWING\s*DOOR", block):
            rec["door_type"] = "swing"
        elif re.search(r"ROLL\s*-?\s*UP", block):
            rec["door_type"] = "rollup"
        if re.search(r"REEF", block):
            rec["class_raw"] = "REEF"
        if h := re.search(r"([\d,]{3,7})\s*(TOTAL|ENG(?:INE)?|ELEC(?:TRIC)?)?\s*(?:HRS|HOURS)", block):
            rec["hours"] = float(h.group(1).replace(",", ""))
            kind = (h.group(2) or "").upper()
            rec["hours_metric"] = "engine" if kind.startswith("ENG") else "electric" if kind.startswith("ELEC") else "total"
        for rx, _fam, _w in ENGINE_RULES:
            if e := re.search(rx, block):
                rec["engine_raw"] = e.group(0)
                break
        if re.search(r"ALLISON", block):
            rec["trans_raw"] = "ALLISON"
        elif t := re.search(r"(\d{1,2})\s*-?\s*SPD|ENDURANT|DT\s*-?12|I-?SHIFT|ULTRASHIFT", block):
            rec["trans_raw"] = t.group(0)
        out.append(rec)
    return out


def parse_catalog_pdf(path_or_file) -> list[dict]:
    import pdfplumber

    with pdfplumber.open(path_or_file) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    return catalog_from_text(text)
