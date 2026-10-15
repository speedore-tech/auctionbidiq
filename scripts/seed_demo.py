"""Seed a demo tenant + SYNTHETIC comps so the app is explorable before real data is loaded.

All synthetic rows carry source_file='DEMO-synthetic' and sale_ids starting with 'D'
so they are easy to spot and purge:  python scripts/seed_demo.py --purge

    python scripts/seed_demo.py            # create demo tenant + synthetic data
    python scripts/seed_demo.py --purge    # remove synthetic comps/sales (keeps your real data)

Demo login: demo@bidiq.local / demo-pass-123   (override with BIDIQ_DEMO_PASSWORD)
"""
from __future__ import annotations

import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from bidiq.auth import hash_password  # noqa: E402
from bidiq.db import SessionLocal, init_db  # noqa: E402
from bidiq.ingest import ensure_sale, ingest_results  # noqa: E402
from bidiq.ingest.results_pdf import ParsedSheet  # noqa: E402
from bidiq.models import IngestLog, RetailFMV, Sale, SoldComp, TargetLot, Tenant, User  # noqa: E402
from bidiq.normalize import enrich  # noqa: E402

DEMO_EMAIL = "demo@bidiq.local"
DEMO_PASSWORD = os.environ.get("BIDIQ_DEMO_PASSWORD", "demo-pass-123")
SRC = "DEMO-synthetic"

# family: (make, model variants, class, desc, engines[(raw, price_adj)], trans, years,
#          price at age 0.5 & zero usage, $/yr, $ per 100k mi (or per 1k hrs), usage/yr, usage kind, n)
FAMILIES = [
    ("FRTL", ["M2 106", "M2 106 BUSINESS CLAS"], "TRUCK", "", [("CUMMINS ISB 6.7 LIT", 0), ("DETROIT DD8", -2500)],
     "ALLISON 2500", range(2014, 2022), 75000, 4200, 3100, 38000, "miles", 70),
    ("INTER", ["MV607"], "TRUCK", "", [("CUMMINS B6.7", 0)], "ALLISON 2500", range(2019, 2023), 80000, 4600,
     3300, 40000, "miles", 30),
    ("INTER", ["4300", "DURASTAR 4300"], "TRUCK", "", [("MAXXFORCE 7", -6000), ("CUMMINS ISB 6.7", 0)],
     "ALLISON AUTO", range(2012, 2019), 62000, 3800, 2600, 30000, "miles", 40),
    ("FRTL", ["M2 112"], "TRUCK", "", [("DETROIT DD8", 0), ("CUMMINS L9", 2500)], "ALLISON 3000", range(2015, 2022),
     80000, 4500, 2900, 45000, "miles", 20),
    ("INTER", ["LT625"], "CONV", "73IN RAISED SLP", [("NAVISTAR A26", 0), ("CUMMINS X15", 6000)], "10 SPD",
     range(2018, 2023), 85000, 5461, 2585, 115000, "miles", 60),
    ("FRTL", ["CASCADIA 126", "CASCADIA"], "CONV", "72IN RAISED SLP", [("DETROIT DD15", 0), ("DETROIT DD13", -3000)],
     "DT12", range(2017, 2023), 95000, 6000, 2800, 110000, "miles", 60),
    ("UTIL", ["3000R"], "REEF TRL", "53FT REEFER", [("THERMO KING S-600", 0), ("CARRIER X4 7300", -1500)], "",
     range(2015, 2022), 70000, 3900, 800, 1500, "hours", 40),
]
LOCS = ["SOUTH SIOUX CITY", "DALLAS", "CHATTANOOGA", "COLUMBUS", "MONTGOMERY", "OKLAHOMA CITY"]


def make_sales(rng: random.Random) -> list[ParsedSheet]:
    sales = []
    start = date(2024, 3, 1)
    for i in range(14):
        sd = start + timedelta(days=int(i * 65 + rng.randint(-10, 10)))
        sales.append(ParsedSheet(sale_id=f"D{sd.year}{i:03d}", sale_date=sd, location=LOCS[i % len(LOCS)]))
    for (make, models, cls, desc, engines, trans, years, base, dpy, dpm, mpy, usage, n) in FAMILIES:
        for _ in range(n):
            sh = rng.choice(sales)
            yr = rng.choice(list(years))
            age = max(0.5, sh.sale_date.year - yr + 0.5)
            eng, eadj = rng.choice(engines)
            row = {"lot_type": "M", "lot_no": None, "year": yr, "make": make, "class_raw": cls,
                   "model_raw": rng.choice(models), "engine_raw": eng, "desc_raw": desc or None,
                   "trans_raw": trans or None, "branded_title": False, "non_operable": False}
            if usage == "miles":
                miles = int(max(5000, rng.gauss(mpy * age, mpy * age * 0.25)))
                row["miles"] = miles
                use_term = dpm * miles / 100_000
            else:
                hrs = round(max(500, rng.gauss(mpy * age, mpy * age * 0.3)), 0)
                row.update(hours=hrs, hours_metric="engine", reefer_engine_hours=hrs, door_type="swing",
                           body_length_ft=53.0)
                use_term = dpm * hrs / 1000
            price = base - dpy * (age - 0.5) - use_term + eadj
            price = price * rng.gauss(1.0, 0.07)
            r = rng.random()
            if r < 0.04:
                row["non_operable"], row["lot_type"], price = True, "P", price * 0.3
            elif r < 0.07:
                row["branded_title"], price = True, price * 0.7
            row["sold_price"] = float(max(2500, round(price / 250) * 250))
            sh.rows.append(row)
    for sh in sales:
        for j, r in enumerate(sh.rows, start=1):
            r["lot_no"] = str(100 + j)
    return sales


def purge(db) -> None:
    db.execute(delete(SoldComp).where(SoldComp.source_file == SRC))
    db.execute(delete(IngestLog).where(IngestLog.filename == SRC))
    db.execute(delete(Sale).where(Sale.sale_id.like("D%")))
    db.commit()


def main() -> None:
    init_db()
    db = SessionLocal()
    if "--purge" in sys.argv:
        purge(db)
        print("Purged synthetic demo comps/sales.")
        return
    purge(db)
    rng = random.Random(2026)
    for sh in make_sales(rng):
        ingest_results(db, sh, None, SRC)

    user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
    if not user:
        t = Tenant(name="Demo — Sohi Transportation", settings={})
        db.add(t)
        db.flush()
        user = User(tenant_id=t.id, email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), role="admin")
        db.add(user)
        db.flush()
    tid = user.tenant_id

    db.execute(delete(RetailFMV).where(RetailFMV.tenant_id == tid, RetailFMV.source == "DEMO"))
    today = date.today()
    fmv = [
        ("FRTL_M2_106", 2018, 2020, 100_000, 250_000, "private", 62000),
        ("FRTL_M2_106", 2018, 2020, 100_000, 250_000, "dealer", 72000),
        ("FRTL_M2_106", 2022, 2023, 0, 100_000, "dealer", 98000),
        ("FRTL_M2_106", 2022, 2023, 0, 100_000, "private", 88000),
        ("INTL_MV607", 2020, 2022, 50_000, 200_000, "private", 74000),
        ("INTL_LT625", 2019, 2021, 300_000, 600_000, "private", 55000),
        ("UTIL_REEFER", 2017, 2019, None, None, "private", 42000),
    ]
    for mg, y0, y1, m0, m1, tier, price in fmv:
        db.add(RetailFMV(tenant_id=tid, region="DFW / TX", model_group=mg, year_lo=y0, year_hi=y1, miles_lo=m0,
                         miles_hi=m1, tier=tier, price=price, is_asking=True, as_of_date=today - timedelta(days=12),
                         source="DEMO", notes="Synthetic demo FMV — replace with real TruckPaper/CTT comps"))

    sale = ensure_sale(db, "D2026900", today + timedelta(days=9), "SOUTH SIOUX CITY", upcoming=True)
    sale.buyers_premium_pct = 5.0
    sale.deposit_amount = 5000
    sale.wire_deadline = "3:00 PM CT the business day before the sale (first-time buyers)"
    sale.payment_window_hours = 24
    db.execute(delete(TargetLot).where(TargetLot.tenant_id == tid, TargetLot.sale_id == "D2026900"))
    targets = [
        dict(lot_no="210", run_order=1, year=2019, make="FRTL", model_raw="M2 106 BUSINESS CLAS",
             engine_raw="CUMMINS ISB 6.7", trans_raw="ALLISON 2500", class_raw="TRUCK", miles=192_000, gvwr=25_999,
             rear_axle_lbs=17_500, body_length_ft=26, liftgate=True, vin="3ALACWFC5KDKE8613",
             condition_notes="26' box, liftgate, clean interior per photos"),
        dict(lot_no="214", run_order=2, year=2019, make="FRTL", model_raw="M2 106", engine_raw="CUMMINS 6.7 LITER",
             trans_raw="ALLISON AUTO", class_raw="TRUCK", miles=248_000, rear_axle_lbs=19_000, body_length_ft=24,
             blind_buy=True, condition_notes="Blind buy — photos only"),
        dict(lot_no="231", run_order=3, year=2023, make="FRTL", model_raw="M2 106", engine_raw="CUMMINS B6.7",
             trans_raw="ALLISON", class_raw="TRUCK", miles=61_000, gvwr=25_999, body_length_ft=26,
             condition_notes="Newer than any comp → retail-minus fallback"),
        dict(lot_no="305", run_order=4, year=2020, make="INTER", model_raw="LT625", engine_raw="NAVISTAR A26",
             trans_raw="10 SPD", class_raw="CONV", desc_raw="73IN RAISED SLP", miles=470_000, apu=True,
             sleeper_size_in=73, blind_buy=True),
        dict(lot_no="402", run_order=5, year=2018, make="UTIL", model_raw="3000R", engine_raw="THERMO KING S-600",
             class_raw="REEF TRL", desc_raw="53FT REEFER", hours=9_800, hours_metric="engine", door_type="swing",
             body_length_ft=53),
    ]
    for t in targets:
        rec = dict(t, sale_id="D2026900", sale_date=sale.sale_date, location=sale.location)
        enrich(rec)
        db.add(TargetLot(tenant_id=tid, checklist={}, photos=[], **{k: v for k, v in rec.items()
                                                                     if hasattr(TargetLot, k)}))
    db.commit()
    n = db.query(SoldComp).filter(SoldComp.source_file == SRC).count()
    print(f"Seeded {n} synthetic comps, demo tenant, FMV rows and 5 target lots for sale D2026900.")
    print(f"Login: {DEMO_EMAIL} / (BIDIQ_DEMO_PASSWORD or the default in this script's docstring)")


if __name__ == "__main__":
    main()
