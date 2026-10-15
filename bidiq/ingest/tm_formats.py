"""Parsers for real Taylor & Martin pre-sale documents (validated on the Pekin, IL 10/15/2026 set):

* Catalog PDF — "LOT NO. DESCRIPTION PRICE" pages. Each lot starts with `<lot> <year> <MAKE> <model…>`;
  "GROUP NO. n - k MATCHED …" blocks give one shared description followed by lot lines of underscores.
  Lot ids: 1..999 (tractors), M-n (straight / box trucks), T-n (trailers), P-n, etc.
* Mileage sheet PDF — a grid of repeated `LOT ODOM ECM` columns, readings in thousands
  ("549" = 549,000; "1+012" = 1,012,000), plus words: scratch, Non Runner, -----, "21,960 hours".
* Reefer hour sheet PDF — a grid of repeated `LOT REEFER HOURS` columns.

Grids are parsed by word x-position (column bucketing), never by text order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ..normalize import normalize_year

LOT_ID = r"(?:[A-Z]{1,2}-\d{1,4}[A-Z]?|\d{1,4}[A-Z]?)"
LOT_RX = re.compile(rf"^{LOT_ID}$")
DATE_RX = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
PLACE_RX = re.compile(r"\b([A-Z][A-Za-z.]+(?: [A-Z][A-Za-z.]+)*),\s*([A-Z]{2})\b")

# Longest first so "WESTERN STAR" wins over a one-word make.
MULTIWORD_MAKES = ["WESTERN STAR", "GREAT DANE", "FONTAINE TRAILER", "LANDOLL", "MAC TRAILER"]


def norm_lot(s: str | None) -> str | None:
    if s is None:
        return None
    v = re.sub(r"[^A-Z0-9-]", "", str(s).upper())
    return v or None


def _date(m: re.Match) -> date | None:
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def location_key(location: str | None) -> str | None:
    """'Pekin, IL' / 'PEKIN' / 'pekin il' -> 'PEKIN' (first word) for matching documents of one sale."""
    if not location:
        return None
    w = re.findall(r"[A-Za-z]+", location.upper())
    return w[0] if w else None


# ================================================================== detection
def detect_kind_from_text(text: str) -> str | None:
    t = text.upper()
    if "PRICE LISTING" in t:
        return "results"
    if "REEFER HOUR" in t or ("REEFER" in t and "HOURS" in t and "LOT" in t and "ODOM" not in t):
        return "reefer"
    if "ODOM" in t and ("MILEAGE" in t or "ECM" in t):
        return "mileage"
    if "LOT NO" in t and "DESCRIPTION" in t:
        return "catalog"
    if "AUCTION" in t:
        return "catalog"
    return None


def detect_pdf_kind(pdf) -> str | None:
    """Look at the first pages only (catalogs are ~100 pages; lots start a few pages in)."""
    seen = []
    for page in pdf.pages[:8]:
        seen.append(page.extract_text() or "")
        k = detect_kind_from_text("\n".join(seen))
        if k and k != "catalog":
            return k
        if k == "catalog" and "LOT NO" in seen[-1].upper():
            return "catalog"
    return detect_kind_from_text("\n".join(seen))


def detect_csv_kind(headers: list[str]) -> str:
    h = set(headers)
    if h & {"sold_price", "price", "hammer"}:
        return "results"
    if h & {"odometer", "odom", "ecm", "ecm_miles", "engine_hours", "total_hours", "reefer_hours"}:
        return "mileage"
    return "catalog"


# ================================================================== catalog
@dataclass
class ParsedCatalog:
    sale_date: date | None = None
    location: str | None = None
    lots: list[dict] = field(default_factory=list)
    blank_lots: list[str] = field(default_factory=list)  # lot numbers listed with no description


_NOISE = re.compile(r"^(LOT NO\.? DESCRIPTION PRICE|#####.*)$")
_FOOTER = re.compile(r"^(\d+\s+)?[A-Z][A-Za-z.]+(?: [A-Z][A-Za-z.]+)*,\s*[A-Z]{2}\s+\d{1,2}/\d{1,2}/\d{2,4}(\s+\d+)?$")
_PRICE_BLANK = re.compile(r"\s*\$_+\s*$")
_UNDERS = re.compile(r"^_{5,}(\s*\$_+)?\s*$")
_LOT_YEAR = re.compile(rf"^({LOT_ID})\s+((?:19|20)\d{{2}})\s+(.+)$")
_LOT_BLANK = re.compile(rf"^({LOT_ID})\s+_{{5,}}")
_GROUP = re.compile(r"^GROUP NO\.\s*\d+")


def _join(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        ln = _PRICE_BLANK.sub("", ln).strip()
        if not ln or _UNDERS.match(ln):
            continue
        if out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln  # "En-" + "gine"
        else:
            out = (out + " " + ln).strip()
    return re.sub(r"\s+", " ", out)


def parse_catalog_text(text: str) -> ParsedCatalog:
    res = ParsedCatalog()
    for m in re.finditer(r"^.*$", text, re.M):
        line = m.group(0).strip()
        if _FOOTER.match(line) and not res.sale_date:
            dm = DATE_RX.search(line)
            pm = PLACE_RX.search(line)
            res.sale_date = _date(dm) if dm else None
            res.location = f"{pm.group(1)}, {pm.group(2)}" if pm else None

    lots: list[tuple[str, list[str]]] = []      # (lot id, description lines)
    group_lines: list[str] | None = None        # shared description of a MATCHED group
    in_group_desc = False
    cur: tuple[str, list[str]] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _NOISE.match(line) or _FOOTER.match(line):
            continue
        if _GROUP.match(line):
            cur = None
            group_lines, in_group_desc = [], True
            continue
        mb = _LOT_BLANK.match(line)
        if mb:
            in_group_desc = False
            cur = None
            if group_lines:
                lots.append((mb.group(1), list(group_lines)))
            else:
                res.blank_lots.append(mb.group(1))
            continue
        my = _LOT_YEAR.match(line)
        if my and not in_group_desc:
            group_lines = None
            cur = (my.group(1), [f"{my.group(2)} {my.group(3)}"])
            lots.append(cur)
            continue
        if _UNDERS.match(line):
            continue
        if in_group_desc and group_lines is not None:
            group_lines.append(line)
        elif cur is not None:
            cur[1].append(line)
    seen = set()
    for lot_id, lines in lots:
        lot = norm_lot(lot_id)
        if lot in seen:
            continue
        seen.add(lot)
        rec = parse_lot_description(lot, _join(lines))
        if rec:
            res.lots.append(rec)
    return res


def _seg(segs: list[str], rx: str) -> str | None:
    for s in segs:
        if re.search(rx, s, re.I):
            return s.strip()
    return None


def parse_lot_description(lot: str, desc: str) -> dict | None:
    m = re.match(r"^((?:19|20)\d{2})\s+(.+)$", desc)
    if not m:
        return None
    year = normalize_year(m.group(1))
    rest = m.group(2)
    up = rest.upper()
    make = next((mk for mk in MULTIWORD_MAKES if up.startswith(mk + " ")), None) or rest.split(" ")[0].upper()
    after = rest[len(make):].strip()
    head = after.split(",")[0]
    model = re.split(r"\b(?:Non-Sleeper|Conventional|Straight Truck|Refrigerated|Cabover|Day Cab)\b", head)[0].strip() or head.strip()
    segs = [s.strip() for s in desc.split(",")]
    rec: dict = {"lot_no": lot, "year": year, "make": make, "model_raw": model[:120],
                 "condition_notes": desc[:4000], "class_raw": "TRL" if lot.startswith("T-") else None}

    eng = _seg(segs, r"\bEngine$")
    if eng:
        rec["engine_raw"] = eng
    tr = _seg(segs, r"\bTransmission$")
    if tr:
        i = segs.index(tr)
        kind = segs[i + 1] if i + 1 < len(segs) and segs[i + 1].startswith("(") else ""
        rec["trans_raw"] = f"{tr} {kind}".strip()
    if mm := re.search(r"(\d{1,2},\d{3}) Lb\. Rear End", desc):
        rec["rear_axle_lbs"] = int(mm.group(1).replace(",", ""))
    if mm := re.search(r"(\d{1,2},\d{3}) Lb\. Front", desc):
        rec["front_axle_lbs"] = int(mm.group(1).replace(",", ""))
    if mm := re.search(r"GVWR?\D{0,6}(\d{2},\d{3})", desc, re.I):
        rec["gvwr"] = int(mm.group(1).replace(",", ""))
    sl = _seg(segs, r"\d+ Inch .*Sleeper")
    if sl and "Non-Sleeper" not in sl:
        rec["sleeper_size_in"] = int(re.search(r"(\d+) Inch", sl).group(1))
    if mm := re.search(r"(\d{2}) Ft\.(?: \d+ Inches?)? Long", desc):
        rec["body_length_ft"] = float(mm.group(1))
    elif mm := re.search(r"(\d{2}) Ft\. [^,]*?(?:Van Body|Body|Bed|Box)", desc):
        rec["body_length_ft"] = float(mm.group(1))
    if re.search(r"\bLiftgate\b|\bLift Gate\b", desc, re.I):
        rec["liftgate"] = True
    if re.search(r"\bAPU\b|Auxiliary Power Unit", desc):
        rec["apu"] = True
    if re.search(r"Engine Brake|Jake Brake", desc):
        rec["engine_brake"] = True
    if re.search(r"Swing (?:Rear )?Doors?", desc):
        rec["door_type"] = "swing"
    elif re.search(r"Rollup", desc):
        rec["door_type"] = "rollup"
    if mm := re.search(r"VIN[:#\s]+([A-HJ-NPR-Z0-9]{17})", desc):
        rec["vin"] = mm.group(1)

    is_trailer = lot.startswith("T-")
    reefer = bool(re.search(r"\bReefer\b|Refrigerated", desc, re.I))
    if is_trailer:
        unit = _seg(segs, r"(Thermo King|Carrier|Precedent|Vector).*Unit$|Unit$")
        if unit:
            rec["engine_raw"] = unit
        rec["body_class"] = "REEFER_TRAILER" if reefer else ("DRY_VAN_TRAILER" if re.search(r"\bVan\b", desc) else "OTHER")
        rec["desc_raw"] = ("REEFER " if reefer else "") + (f"{int(rec['body_length_ft'])}FT" if rec.get("body_length_ft") else "")
    elif re.search(r"Straight Truck", desc):
        rec["body_class"] = "BOX_TRUCK" if re.search(r"Van Body|Box", desc) else "STRAIGHT_TRUCK"
        rec["desc_raw"] = (_seg(segs, r"Ft\. .*(Body|Bed)") or "")[:120] or None
    elif re.search(r"Transporter|Pickup|Pick-Up|Crew Cab", desc, re.I) or lot.startswith("P-"):
        rec["body_class"] = "OTHER"
    elif re.search(r"Non-Sleeper", desc):
        rec["body_class"] = "TRACTOR_DAYCAB"
        rec["desc_raw"] = "DAY CAB"
    elif re.search(r"Sleeper|Upper Bunk|Bunk Heater", desc):
        rec["body_class"] = "TRACTOR_SLEEPER"
        rec["desc_raw"] = (sl or "SLEEPER")[:120]
    else:
        rec["body_class"] = "OTHER"
    return rec


def parse_catalog_pdf(path_or_file) -> ParsedCatalog:
    import pdfplumber

    with pdfplumber.open(path_or_file) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    return parse_catalog_text(text)


# ================================================================== meter-reading grids
@dataclass
class ParsedReadings:
    kind: str                       # mileage | reefer
    sale_date: date | None = None
    location: str | None = None
    rows: list[dict] = field(default_factory=list)


def _thousands(tok: str) -> int | None:
    """Mileage sheet reading in thousands: '549' -> 549000, '1+012' -> 1012000, '005' -> 5000."""
    t = tok.replace(",", "")
    if re.fullmatch(r"\d+\+\d{3}", t):
        a, b = t.split("+")
        return (int(a) * 1000 + int(b)) * 1000
    if re.fullmatch(r"\d{1,4}", t):
        return int(t) * 1000
    return None


def _group_lines(words: list[dict], tol: float = 3.0) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in lines]


def parse_readings_words(pages_words: list[list[dict]], header_text: str, tolerance_pct: float = 10.0) -> ParsedReadings:
    up = header_text.upper()
    kind = "reefer" if "REEFER" in up and "ODOM" not in up else "mileage"
    out = ParsedReadings(kind=kind)
    dates = [d for d in (_date(m) for m in DATE_RX.finditer(header_text)) if d]
    out.sale_date = max(dates) if dates else None    # header shows "UPDATED: <day before>" and the sale date
    pm = PLACE_RX.search(header_text)
    out.location = f"{pm.group(1)}, {pm.group(2)}" if pm else None

    by_lot: dict[str, dict] = {}
    for words in pages_words:
        lines = _group_lines(words)
        hdr = next((l for l in lines if sum(1 for w in l if w["text"] == "LOT") >= 1 and
                    any(w["text"] in ("ODOM", "REEFER", "ECM") for w in l)), None)
        if not hdr:
            continue
        lot_x = [w["x0"] for w in hdr if w["text"] == "LOT"]
        width = (lot_x[1] - lot_x[0]) if len(lot_x) > 1 else 120
        bounds = [(x - 8, (lot_x[i + 1] - 8) if i + 1 < len(lot_x) else x - 8 + width) for i, x in enumerate(lot_x)]
        col_x: list[dict] = []
        for lo, hi in bounds:
            cx = {w["text"]: w["x0"] for w in hdr if lo <= w["x0"] < hi and w["text"] in ("ODOM", "ECM", "REEFER")}
            col_x.append(cx)
        hdr_top = hdr[0]["top"]
        for line in lines:
            if line[0]["top"] <= hdr_top + 2:
                continue
            for gi, (lo, hi) in enumerate(bounds):
                toks = [w for w in line if lo <= w["x0"] < hi]
                if not toks or not LOT_RX.match(toks[0]["text"]):
                    # continuation of the previous line's cell (value wrapped onto a new line)
                    continue
                lot = norm_lot(toks[0]["text"])
                rec = by_lot.setdefault(lot, {"lot_no": lot})
                _cell(rec, toks[1:], col_x[gi], kind)
        # second pass: orphan values on wrapped lines (e.g. lot 303 then '843' a pixel lower)
        for li, line in enumerate(lines):
            for gi, (lo, hi) in enumerate(bounds):
                toks = [w for w in line if lo <= w["x0"] < hi]
                if toks and not LOT_RX.match(toks[0]["text"]) and li > 0 and line[0]["top"] > hdr_top + 2:
                    prev = [w for w in lines[li - 1] if lo <= w["x0"] < hi]
                    if prev and LOT_RX.match(prev[0]["text"]) and abs(line[0]["top"] - lines[li - 1][0]["top"]) < 9:
                        rec = by_lot.get(norm_lot(prev[0]["text"]))
                        if rec is not None and not _has_values(rec):
                            _cell(rec, toks, col_x[gi], kind)
    for rec in by_lot.values():
        o, e = rec.get("odometer_miles"), rec.get("ecm_miles")
        rec["tamper_flag"] = bool(o and e and abs(o - e) / max(o, e) * 100 > tolerance_pct)
        out.rows.append(rec)
    return out


def _has_values(rec: dict) -> bool:
    return any(rec.get(k) is not None for k in ("odometer_miles", "ecm_miles", "reefer_total_hours", "meter_hours", "status"))


def _cell(rec: dict, toks: list[dict], cols: dict, kind: str) -> None:
    texts = [t["text"] for t in toks]
    low = " ".join(texts).lower()
    notes = []
    if "scratch" in low:
        rec["status"] = "scratch"
    if "non runner" in low or "non-runner" in low:
        rec["status"] = "non_runner"
    i = 0
    while i < len(toks):
        t = toks[i]["text"]
        nxt = toks[i + 1]["text"].lower() if i + 1 < len(toks) else ""
        if re.fullmatch(r"-{2,}", t):
            i += 1
            continue
        if m := re.fullmatch(r"([\d,]+)mi", t, re.I):   # "662mi" — raw miles glued to the unit
            toks = toks[:i] + [dict(toks[i], text=m.group(1)), dict(toks[i], text="mi")] + toks[i + 1:]
            continue
        if re.fullmatch(r"[\d,]+(\+\d{3})?", t):
            if nxt.startswith("hour"):
                rec["meter_hours"] = float(t.replace(",", ""))
                i += 2
                continue
            if kind == "reefer":
                rec["reefer_total_hours"] = float(t.replace(",", ""))
                i += 1
                continue
            raw_miles = nxt == "mi"
            val = int(t.replace(",", "")) if raw_miles else _thousands(t)
            if val is not None:
                x = toks[i]["x0"]
                field_name = "odometer_miles"
                if "ODOM" in cols and "ECM" in cols:
                    field_name = "ecm_miles" if abs(x - cols["ECM"]) < abs(x - cols["ODOM"]) else "odometer_miles"
                if rec.get(field_name) is not None:   # both values landed nearest one header
                    field_name = "ecm_miles" if field_name == "odometer_miles" else "odometer_miles"
                rec[field_name] = val
            i += 2 if raw_miles else 1
            continue
        if t.lower() not in ("scratch", "non", "runner", "mi", "hours"):
            notes.append(t)
        i += 1
    if notes:
        rec["note"] = " ".join(notes)[:120]


def parse_readings_pdf(path_or_file, tolerance_pct: float = 10.0) -> ParsedReadings:
    import pdfplumber

    with pdfplumber.open(path_or_file) as pdf:
        pages = [p.extract_words(x_tolerance=1.5, keep_blank_chars=False) for p in pdf.pages]
        header = pdf.pages[0].extract_text() or ""
    return parse_readings_words(pages, header[:600], tolerance_pct)


def parse_any_date(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None
