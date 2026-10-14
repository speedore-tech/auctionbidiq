"""T&M "PRICE LISTING BY TYPE/LOT#" result-sheet parser (spec §5.1).

Fixed-width, column-positioned sheets are parsed with pdfplumber **coordinate
bucketing** — every word is assigned to a column by its x0 — never by naive text
slicing (engine/sleeper/transmission sub-fields share the description band).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ..normalize import normalize_year

# Column x-ranges validated on the T&M 2026xxx format. (lo inclusive, hi exclusive)
COLUMNS: list[tuple[str, float, float]] = [
    ("type", -1e9, 30),
    ("lot", 30, 60),
    ("year", 60, 90),
    ("make", 90, 120),
    ("class", 120, 160),
    ("model", 160, 291),
    ("engine", 291, 430),
    ("desc", 430, 525),
    ("trans", 525, 625),  # overlaps miles 600-690; numeric tokens are claimed by miles first
]
BRANDED_X = (660, 700)
NONOP_MIN_X = 700
MILES_X = (600, 690)

PRICE_RX = re.compile(r"^\$?[\d,]+\.\d{2}$")
MILES_RX = re.compile(r"^[\d,]+$")
HEADER_RX = re.compile(r"SALE:\s*(\d+)\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+(.+?)\s*$")
LINE_TOL = 3.0


@dataclass
class ParsedSheet:
    sale_id: str | None = None
    sale_date: date | None = None
    location: str | None = None
    rows: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _parse_date(s: str) -> date | None:
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def group_lines(words: list[dict], tol: float = LINE_TOL) -> list[list[dict]]:
    """Group pdfplumber words into visual lines by their `top` coordinate."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def bucket_line(line: list[dict]) -> dict | None:
    """Assign words of one line to columns. Returns a raw row dict or None if not a lot row."""
    cols: dict[str, list[str]] = {c: [] for c, _, _ in COLUMNS}
    price = None
    branded = False
    non_op = False
    miles = None
    for w in line:
        t = w["text"].strip()
        x = w["x0"]
        if not t:
            continue
        if PRICE_RX.match(t) and x > NONOP_MIN_X - 60:
            price = float(t.replace("$", "").replace(",", ""))
            continue
        if t.upper() == "Y" and BRANDED_X[0] <= x < BRANDED_X[1]:
            branded = True
            continue
        if t.upper() == "Y" and x >= NONOP_MIN_X:
            non_op = True
            continue
        # In the trans/miles overlap (600-625) only a comma number counts as miles,
        # so "10 SPD" / "ALLISON 2500" bleed stays in trans.
        if MILES_X[0] <= x < MILES_X[1] and MILES_RX.match(t) and ("," in t or x >= 625):
            miles = int(t.replace(",", ""))
            continue
        for name, lo, hi in COLUMNS:
            if lo <= x < hi:
                cols[name].append(t)
                break
    lot = " ".join(cols["lot"]).strip()
    year = normalize_year(" ".join(cols["year"]).strip())
    if not lot or not re.match(r"^[A-Z]?\d+[A-Z]?$", lot) or year is None or price is None:
        return None
    return {
        "lot_type": (" ".join(cols["type"]).strip() or None),
        "lot_no": lot,
        "year": year,
        "make": " ".join(cols["make"]) or None,
        "class_raw": " ".join(cols["class"]) or None,
        "model_raw": " ".join(cols["model"]) or None,
        "engine_raw": " ".join(cols["engine"]) or None,
        "desc_raw": " ".join(cols["desc"]) or None,
        "trans_raw": " ".join(cols["trans"]) or None,
        "miles": miles,
        "branded_title": branded,
        "non_operable": non_op,
        "sold_price": price,
    }


def parse_words_pages(pages_words: list[list[dict]]) -> ParsedSheet:
    out = ParsedSheet()
    for words in pages_words:
        for line in group_lines(words):
            text = " ".join(w["text"] for w in line)
            hm = HEADER_RX.search(text)
            if hm:
                out.sale_id = out.sale_id or hm.group(1)
                out.sale_date = out.sale_date or _parse_date(hm.group(2))
                out.location = out.location or re.sub(r"\s+PAGE.*$", "", hm.group(3)).strip()
                continue
            row = bucket_line(line)
            if row:
                out.rows.append(row)
            elif re.search(r"\d+\.\d{2}\b", text) and re.match(r"^\s*[A-Z]?\s*\d", text):
                out.skipped.append(text)  # looked like a lot row but didn't parse — surface it
    return out


def parse_results_pdf(path_or_file) -> ParsedSheet:
    import pdfplumber

    with pdfplumber.open(path_or_file) as pdf:
        pages = [p.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5) for p in pdf.pages]
    return parse_words_pages(pages)
