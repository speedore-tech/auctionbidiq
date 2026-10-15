"""Render rows as a T&M-style 'PRICE LISTING BY TYPE/LOT#' PDF at the spec's column x-positions.

Used by tests and to try the upload path without a real result sheet:
    python scripts/make_sample_pdf.py out.pdf
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas

# x where each field is drawn (inside the spec's column ranges)
X = {"type": 12, "lot": 33, "year": 63, "make": 92, "class": 122, "model": 162, "engine": 293, "desc": 432,
     "trans": 527, "miles": 630, "branded": 670, "nonop": 703, "price": 722}


def render(path: str | Path, header: str, rows: list[dict]) -> None:
    w, h = landscape(letter)
    c = canvas.Canvas(str(path), pagesize=(w, h))
    y = None

    def new_page():
        nonlocal y
        c.setFont("Helvetica-Bold", 8)
        c.drawString(12, h - 30, "PRICE LISTING BY TYPE/LOT#")
        c.drawString(260, h - 30, header)
        c.setFont("Helvetica", 6.5)
        y = h - 55

    new_page()
    for r in rows:
        if y < 40:
            c.showPage()
            new_page()
        for key in ("type", "lot", "year", "make", "class", "model", "engine", "desc", "trans"):
            if r.get(key):
                c.drawString(X[key], y, str(r[key]))
        if r.get("miles") is not None:
            c.drawString(X["miles"], y, f"{r['miles']:,}")
        if r.get("branded"):
            c.drawString(X["branded"], y, "Y")
        if r.get("nonop"):
            c.drawString(X["nonop"], y, "Y")
        c.drawString(X["price"], y, f"{r['price']:,.2f}")
        y -= 11
    c.save()


SAMPLE_ROWS = [
    {"lot": "101", "year": "2019", "make": "FRTL", "class": "TRUCK", "model": "M2 106 BUSINESS CLAS",
     "engine": "CUMMINS ISB 6.7 LIT", "desc": "", "trans": "ALLISON 2500", "miles": 186_420, "price": 41_500},
    {"lot": "102", "year": "2019", "make": "FRTL", "class": "TRUCK", "model": "M2 106",
     "engine": "CUMMINS 6.7 LITER", "desc": "", "trans": "ALLISON AUTO", "miles": 221_004, "price": 38_000},
    {"lot": "103", "year": "2020", "make": "INTER", "class": "TRUCK", "model": "MV607",
     "engine": "CUMMINS B6.7", "desc": "", "trans": "ALLISON 2500", "miles": 143_877, "price": 47_250},
    {"lot": "104", "year": "2020", "make": "INTER", "class": "CONV", "model": "LT625",
     "engine": "NAVISTAR A26", "desc": "73IN RAISED SLP", "trans": "10 SPD", "miles": 416_000, "price": 31_000},
    {"lot": "105", "year": "2020", "make": "INTER", "class": "CONV", "model": "LT625",
     "engine": "CUMMINS X15", "desc": "73IN RAISED SLP", "trans": "ENDURANT", "miles": 1_004_000, "price": 20_000},
    {"lot": "106", "year": "2017", "make": "FRTL", "class": "TRUCK", "model": "M2 106",
     "engine": "CUMMINS ISB 6.7", "desc": "", "trans": "ALLISON", "miles": 301_000, "nonop": True, "price": 9_000},
    {"lot": "107", "year": "2018", "make": "FRTL", "class": "TRUCK", "model": "M2 112",
     "engine": "DETROIT DD8", "desc": "", "trans": "ALLISON 3000", "miles": 255_500, "branded": True, "price": 22_500},
    # trailer lots carry a "T -" prefix in the TYPE column; reefer rows put unit HOURS in MILES/HOURS
    {"type": "T -", "lot": "101", "year": "2019", "make": "UTILI", "class": "REEFR", "model": "53FT. ALUMINUM REEFER",
     "engine": "CARRIER X4 7300 UNIT", "desc": "SWING DOORS", "trans": "", "miles": 19_806, "price": 21_000},
]

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_results_2026055.pdf"
    render(out, "SALE: 2026055 05/14/2026 SOUTH SIOUX CITY", SAMPLE_ROWS)
    print("wrote", out)
