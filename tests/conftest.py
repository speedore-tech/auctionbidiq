import os
import tempfile
from pathlib import Path

# Point the app at a throwaway DB *before* bidiq is imported anywhere.
_TMP = Path(tempfile.mkdtemp(prefix="bidiq_test_"))
os.environ["BIDIQ_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["BIDIQ_UPLOAD_DIR"] = str(_TMP / "uploads")

import random  # noqa: E402
from datetime import date  # noqa: E402

import pytest  # noqa: E402

from bidiq.config import merged_settings  # noqa: E402
from bidiq.normalize import enrich  # noqa: E402


@pytest.fixture
def settings():
    return merged_settings({})


def make_comps(model_raw="LT625", make="INTER", engine="NAVISTAR A26", class_raw="CONV", desc="73IN RAISED SLP",
               n=60, base=85000, per_year=5461, per_100k=2585, mpy=115000, years=range(2018, 2023), seed=1,
               sale_date=date(2026, 5, 1), noise=0.03):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        yr = rng.choice(list(years))
        age = max(0.5, sale_date.year - yr + 0.5)
        miles = int(max(5000, rng.gauss(mpy * age, mpy * age * 0.3)))
        price = (base - per_year * (age - 0.5) - per_100k * miles / 100_000) * rng.gauss(1, noise)
        rec = {"id": i, "sale_id": f"S{i % 6}", "sale_date": sale_date, "lot_no": str(100 + i), "year": yr,
               "make": make, "model_raw": model_raw, "engine_raw": engine, "class_raw": class_raw, "desc_raw": desc,
               "miles": miles, "sold_price": round(price, 0), "branded_title": False, "non_operable": False}
        out.append(enrich(rec))
    return out
