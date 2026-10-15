"""Small fixtures that mirror the real Taylor & Martin pre-sale document layouts
(Pekin, IL 10/15/2026 catalog, mileage sheet and reefer-hour sheet)."""
from __future__ import annotations

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

CATALOG_TEXT = """LOT NO. DESCRIPTION PRICE
2 2024 PETERBILT 579 UltraCab Conventional, 72 Inch Unibilt High Roof Sleeper, Up-
per Bunk, Cummins X15 Engine, 450 H.P., Engine Brake, Automatic Transmission,
40,000 Lb. Rear Ends, 2.64 Ratio, Air Ride Suspension, Aluminum
Disc Wheels $_______________
GROUP NO. 1 - 3 MATCHED 2024 FREIGHTLINERs
2024 FREIGHTLINER Cascadia126 Conventional, 72 Inch Raised Roof Sleeper, Upper
Bunk, Detroit DD15 Engine, 450 H.P., Engine Brake, DT12 Transmission, (Automatic),
40,000 Lb. Rear Ends, 2.15 Ratio, Air Ride Suspension, 22.5 Low Profile Tires
8 ___________________________________________________________________________ $_______________
9 ___________________________________________________________________________ $_______________
10 ___________________________________________________________________________ $_______________
Pekin, IL 10/15/26 2
LOT NO. DESCRIPTION PRICE
43 2024 VOLVO VNR 300 Non-Sleeper Conventional, Volvo D11 Engine, 425 H.P., En-
gine Brake, Volvo I-Shift ATO2612F Transmission, (Automatic), 40,000 Lb. Rear Ends $_______________
M-4 2023 FREIGHTLINER M2 106 Business Class Conventional Straight Truck, Cummins ISB
6.7 Liter Engine, 200 H.P., Allison Automatic Transmission, 19,000 Lb. Rear
End, Single Axle, 26 Ft. Van Body, Rollup Door, Liftgate, 270 Inch Wheel Base $_______________
328 ___________________________________________________________________________ $_______________
3 Pekin, IL 10/15/26
GROUP NO. 14 - 2 MATCHED 2017 GREAT DANEs
2017 GREAT DANE Multi-Temp Aluminum Reefer, 53 Ft. Long, 102 Inches Wide, 13 Ft. 6 Inches
High, Thermo King Precedent S-600M Unit, Aluminum Flat Floor, Rollup Door, Sliding Tandem
T-29 ___________________________________________________________________________ $_______________
T-30 ___________________________________________________________________________ $_______________
T-90 2015 UTILITY 3000R Aluminum Reefer, 53 Ft. Long, 102 Inches Wide, Carrier Vector 8600MT
Unit, Swing Doors $_______________
"""


def render_catalog_pdf(path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 750
    for line in CATALOG_TEXT.splitlines():
        if y < 40:
            c.showPage()
            y = 750
        c.setFont("Helvetica", 8)
        c.drawString(30, y, line)
        y -= 11
    c.save()


def _grid_pdf(path, title_lines, headers, groups, rows):
    """groups: list of x positions where each LOT column starts; headers: words after LOT with x offsets."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 8)
    y = 760
    for t in title_lines:
        c.drawString(160, y, t)
        y -= 12
    y -= 10
    for gx in groups:
        c.drawString(gx, y, "LOT")
        for word, dx in headers:
            c.drawString(gx + dx, y, word)
    y -= 13
    for row in rows:                       # row: list per group of [(text, dx), ...]
        for gx, cell in zip(groups, row):
            for text, dx in cell:
                c.drawString(gx + dx, y, text)
        y -= 13
    c.save()


def render_mileage_pdf(path) -> None:
    g = [22, 115, 210]
    rows = [
        [[("1", 6), ("scratch", 20)], [("51", 0), ("549", 25), ("553", 58)], [("160", 0), ("1+012", 25)]],
        [[("2", 6), ("270", 26), ("258", 57)], [("52", 0), ("Non", 25), ("Runner", 42)], [("210", 0), ("664", 25), ("662", 58), ("mi", 72)]],
        [[("8", 6), ("461", 26), ("461", 57)], [("62", 0), ("411", 25), ("234", 58)], [("M-12", 0), ("21,960", 25), ("hours", 52)]],
        [[("9", 6), ("475", 26), ("-----", 57)], [("M-4", 0), ("194", 25), ("194", 58)], []],
    ]
    _grid_pdf(path, ["MILEAGE INFO SHEET UPDATED: 10/14/2026 Pekin, IL", "(Mileages displayed in thousandths) 10/15/2026"],
              [("ODOM", 21), ("ECM", 55)], g, rows)


def render_reefer_pdf(path) -> None:
    g = [22, 120]
    rows = [
        [[("T-29", 2), ("18,189", 27)], [("T-90", 0), ("25,345", 32)]],
        [[("T-30", 2), ("Non", 27), ("Runner", 45)], [("T-91", 0), ("scratch", 32)]],
    ]
    _grid_pdf(path, ["Reefer Hour Sheet UPDATED: 10/14/2026 Pekin,IL", "4:00 PM (Central) 10/15/2026"],
              [("REEFER", 25)], g, rows)
