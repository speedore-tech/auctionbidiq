from fastapi.testclient import TestClient

from bidiq.api.main import app
from bidiq.db import init_db
from scripts.make_sample_pdf import SAMPLE_ROWS, render

init_db()
client = TestClient(app)


def _register(email, tenant):
    r = client.post("/api/auth/register", json={"tenant_name": tenant, "email": email, "password": "test-pass-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_end_to_end(tmp_path):
    a = _register("a@example.test", "Tenant A")
    b = _register("b@example.test", "Tenant B")
    assert client.get("/api/dashboard").status_code == 401

    pdf = tmp_path / "res.pdf"
    render(pdf, "SALE: 2026055 05/14/2026 SOUTH SIOUX CITY", SAMPLE_ROWS)
    up = lambda h, vis: client.post("/api/ingest/results", headers=h, data={"visibility": vis},
                                    files={"file": ("res.pdf", pdf.read_bytes(), "application/pdf")})
    r = up(a, "private")
    assert r.status_code == 200 and r.json()["rows"] == len(SAMPLE_ROWS)
    r2 = up(a, "private")  # idempotent re-ingest
    assert r2.json()["replaced"] == len(SAMPLE_ROWS)
    assert len(client.get("/api/comps", headers=a).json()) == len(SAMPLE_ROWS)
    assert len(client.get("/api/comps", headers=b).json()) == 0  # private comps isolated

    # catalog CSV -> target lots
    csv = b"lot_no,year,make,model,engine,trans,miles,gvwr,vin\n210,2019,FRTL,M2 106,CUMMINS ISB 6.7,ALLISON,190000,25999,1M8GDM9AXKP042788\n"
    r = client.post("/api/ingest/catalog", headers=a, data={"sale_id": "2026101", "location": "DALLAS"},
                    files={"file": ("cat.csv", csv, "text/csv")})
    assert r.json()["created"] == 1
    targets = client.get("/api/targets", headers=a).json()
    assert len(targets) == 1 and targets[0]["model_group"] == "FRTL_M2_106"
    assert client.get("/api/targets", headers=b).json() == []
    tid = targets[0]["id"]
    assert client.get(f"/api/targets/{tid}/recommendation", headers=b).status_code == 404

    rec = client.get(f"/api/targets/{tid}/recommendation", headers=a).json()
    assert rec["recommendation"]["ok"], rec
    assert rec["risk"]["vin"]["check_digit_ok"]
    assert rec["risk"]["cdl"]["cdl_required"] is False

    wi = client.post("/api/whatif", headers=a, json={"target_id": tid, "overrides": {"buyers_premium_pct": 10}}).json()
    assert wi["recommendation"]["cost_basis"]["buyers_premium_pct"] == 10

    sheet = client.get("/api/bidsheet.csv?sale_id=2026101", headers=a)
    assert sheet.status_code == 200 and "target_hammer" in sheet.text and "210" in sheet.text

    o = client.post("/api/outcomes", headers=a, json={"target_lot_id": tid, "did_bid": True, "won": True,
                                                       "final_hammer": 39000}).json()
    assert o["predicted_p50"] is not None
    assert client.get("/api/outcomes", headers=a).json()["calibration"]["n"] == 1

    # retail FMV rejects auction sources for retail tiers
    bad = client.post("/api/fmv", headers=a, json={"model_group": "FRTL_M2_106", "year_lo": 2018, "year_hi": 2020,
                                                   "tier": "private", "price": 60000, "source": "Ritchie Bros auction"})
    assert bad.status_code == 400


def test_user_management():
    owner = _register("owner@example.test", "Owner Co")
    # make the owner a platform superadmin for this test
    import bidiq.auth as auth_mod
    auth_mod.SUPERADMIN_EMAILS.add("owner@example.test")
    try:
        r = client.post("/api/users", headers=owner, json={"email": "teammate@example.test", "password": "team-pass-123",
                                                            "role": "member", "workspace": "mine"})
        assert r.status_code == 200, r.text
        r = client.post("/api/users", headers=owner, json={"email": "partner@example.test", "password": "partner-pass-1",
                                                            "role": "admin", "workspace": "new", "workspace_name": "Partner LLC"})
        assert r.status_code == 200 and r.json()["tenant"] == "Partner LLC"
        assert client.post("/api/users", headers=owner, json={"email": "teammate@example.test", "password": "x" * 9}).status_code == 400

        mate = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"email": "teammate@example.test", "password": "team-pass-123"}).json()["token"]}
        assert client.get("/api/users", headers=mate).status_code == 403          # members can't manage logins
        assert client.put("/api/settings", headers=mate, json={"aggressiveness": 0.9}).status_code == 403
        t = client.post("/api/targets", headers=owner, json={"year": 2019, "make": "FRTL", "model_raw": "M2 106"}).json()
        assert any(x["id"] == t["id"] for x in client.get("/api/targets", headers=mate).json())  # same workspace shares targets

        partner = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"email": "partner@example.test", "password": "partner-pass-1"}).json()["token"]}
        assert not any(x["id"] == t["id"] for x in client.get("/api/targets", headers=partner).json())
        users_seen = client.get("/api/users", headers=partner).json()["users"]
        assert [u["email"] for u in users_seen] == ["partner@example.test"]      # partner admin sees only own workspace
        mate_id = next(u["id"] for u in client.get("/api/users", headers=owner).json()["users"] if u["email"] == "teammate@example.test")
        assert client.put(f"/api/users/{mate_id}", headers=partner, json={"password": "hijack-pass-1"}).status_code == 404

        # reset password signs the user out; new password works
        assert client.put(f"/api/users/{mate_id}", headers=owner, json={"password": "new-team-pass"}).status_code == 200
        assert client.get("/api/auth/me", headers=mate).status_code == 401
        assert client.post("/api/auth/login", json={"email": "teammate@example.test", "password": "new-team-pass"}).status_code == 200

        # change own password
        assert client.post("/api/auth/password", headers=owner, json={"current_password": "wrong", "new_password": "abcdefgh1"}).status_code == 400
        assert client.post("/api/auth/password", headers=owner, json={"current_password": "test-pass-123", "new_password": "owner-pass-2"}).status_code == 200

        assert client.delete(f"/api/users/{mate_id}", headers=owner).status_code == 200
        assert client.post("/api/auth/login", json={"email": "teammate@example.test", "password": "new-team-pass"}).status_code == 401
    finally:
        auth_mod.SUPERADMIN_EMAILS.discard("owner@example.test")


def test_tm_presale_documents_link_by_date(tmp_path):
    from .tm_fixtures import render_catalog_pdf, render_mileage_pdf, render_reefer_pdf
    h = _register("pekin@example.test", "Pekin buyer")
    cat, mil, ref = tmp_path / "10-15-26-Pekin-Catalog.pdf", tmp_path / "2026062PekinMileageSheet.pdf", tmp_path / "2026062PekinReeferHr.pdf"
    render_catalog_pdf(cat), render_mileage_pdf(mil), render_reefer_pdf(ref)
    up = lambda path, kind, data=None: client.post(f"/api/ingest/{kind}", headers=h, data=data or {},
                                                   files={"file": (path.name, path.read_bytes(), "application/pdf")})

    # sheets first (no catalog yet) — dropped in the WRONG box: still detected and stored
    r = up(mil, "catalog", {"sale_id": "2026062"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "mileage" and "note" in r.json() and r.json()["matched"] == 0
    # catalog has no sale number in its name: linked to the sheets by date + location, and takes the official id
    r = up(cat, "mileage")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["kind"] == "catalog" and j["created"] == 9 and j["sale_id"] == "2026062" and j["readings_applied"] >= 3
    r = up(ref, "mileage")
    assert r.json()["kind"] == "reefer" and r.json()["matched"] == 3

    lots = {t["lot_no"]: t for t in client.get("/api/targets?sale_id=2026062", headers=h).json()}
    assert lots["2"]["miles"] == 270_000 and lots["2"]["sale_date"] == "2026-10-15"
    assert lots["M-4"]["miles"] == 194_000
    assert lots["T-29"]["hours"] == 18189 and lots["T-29"]["hours_metric"] == "sheet"
    assert lots["T-30"]["non_operable"] is True
    sheet = client.get("/api/bidsheet?sale_id=2026062", headers=h).json()["rows"]
    assert len(sheet) == 9 and all("ok" in r for r in sheet)


def test_catalog_without_sheets_gets_placeholder_then_official_id(tmp_path):
    from .tm_fixtures import render_catalog_pdf, render_mileage_pdf
    h = _register("pekin2@example.test", "Pekin buyer 2")
    cat, mil = tmp_path / "catalog.pdf", tmp_path / "2026062PekinMileageSheet.pdf"
    render_catalog_pdf(cat), render_mileage_pdf(mil)
    r = client.post("/api/ingest/auto", headers=h, files={"file": (cat.name, cat.read_bytes(), "application/pdf")}).json()
    assert r["sale_id"] in ("PEKIN-20261015", "2026062")   # 2026062 if another tenant already created that sale
    r = client.post("/api/ingest/auto", headers=h, data={"sale_id": "2026062"},
                    files={"file": (mil.name, mil.read_bytes(), "application/pdf")}).json()
    assert r["matched"] >= 3 and r["sale_id"] == "2026062"
    assert {t["sale_id"] for t in client.get("/api/targets", headers=h).json()} == {"2026062"}


def test_duplicates_prevented_and_delete_reload(tmp_path):
    from bidiq.services import load_comps
    from bidiq.db import SessionLocal
    from bidiq.config import merged_settings
    from .tm_fixtures import render_catalog_pdf
    import bidiq.auth as auth_mod
    h = _register("dup@example.test", "Dup Co")
    me = client.get("/api/auth/me", headers=h).json()
    pdf = tmp_path / "res.pdf"
    render(pdf, "SALE: 2026999 09/17/2026 HAMPTON", SAMPLE_ROWS)
    up = lambda vis: client.post("/api/ingest/results", headers=h, data={"visibility": vis},
                                 files={"file": ("res.pdf", pdf.read_bytes(), "application/pdf")})
    assert up("shared").status_code == 200 and up("private").status_code == 200
    db = SessionLocal()
    mine = [c for c in load_comps(db, me["tenant_id"], merged_settings({})) if c["sale_id"] == "2026999"]
    assert len(mine) == len(SAMPLE_ROWS)                      # loaded in both pools, counted once
    summ = client.get("/api/data/summary", headers=h).json()
    row = next(r for r in summ["comps"] if r["sale_id"] == "2026999")
    assert row["duplicated"] and row["shared"] == row["private"] == len(SAMPLE_ROWS)

    # catalog uploaded under two different typed ids -> one set of lots, not two
    cat = tmp_path / "catalog.pdf"
    render_catalog_pdf(cat)
    send = lambda sid: client.post("/api/ingest/auto", headers=h, data={"sale_id": sid},
                                   files={"file": ("catalog.pdf", cat.read_bytes(), "application/pdf")}).json()
    assert send("20261015")["created"] == 9
    second = send("2026062")
    assert second["created"] == 0 and second["sale_id"] == "20261015"
    assert len(client.get("/api/targets", headers=h).json()) == 9

    # deleting shared comps needs the platform owner; private is fine for a workspace admin
    assert client.delete("/api/data/comps?scope=shared&sale_id=2026999", headers=h).status_code == 403
    assert client.delete("/api/data/comps?scope=private", headers=h).json()["deleted"] == len(SAMPLE_ROWS)
    auth_mod.SUPERADMIN_EMAILS.add("dup@example.test")
    try:
        assert client.delete("/api/data/comps?scope=shared&sale_id=2026999", headers=h).json()["deleted"] == len(SAMPLE_ROWS)
    finally:
        auth_mod.SUPERADMIN_EMAILS.discard("dup@example.test")
    assert not any(r["sale_id"] == "2026999" for r in client.get("/api/data/summary", headers=h).json()["comps"])
    assert client.delete("/api/data/lots?sale_id=20261015", headers=h).json()["deleted"] == 9
    assert client.get("/api/targets", headers=h).json() == []
    assert up("shared").json()["rows"] == len(SAMPLE_ROWS)    # reload works after delete


def test_auction_day_analysis(tmp_path):
    from .conftest import make_comps
    h = _register("analyst@example.test", "Analyst Co")
    # two sales of LT625s: the later one is analysed using only the earlier one
    import csv as _csv
    def sheet(path, sale_id, d, comps):
        with open(path, "w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["sale_id", "sale_date", "location", "lot_no", "year", "make", "class", "model", "engine", "desc", "miles", "sold_price"])
            for c in comps:
                w.writerow([sale_id, d, "PEKIN", c["lot_no"], c["year"], "INTER", "CONV", "LT625", "NAVISTAR A26", "73IN RAISED SLP", c["miles"], c["sold_price"]])
    early, late = tmp_path / "a.csv", tmp_path / "b.csv"
    sheet(early, "3000001", "2026-07-01", make_comps(n=60, seed=7))
    cheap = [dict(c, sold_price=round(c["sold_price"] * 0.6)) for c in make_comps(n=20, seed=8)]   # a cold sale
    sheet(late, "3000002", "2026-08-01", cheap)
    for f in (early, late):
        assert client.post("/api/ingest/results", headers=h, data={"visibility": "private"},
                           files={"file": (f.name, f.read_bytes(), "text/csv")}).status_code == 200
    sales = client.get("/api/analysis/sales", headers=h).json()
    assert {"3000001", "3000002"} <= {s["sale_id"] for s in sales}
    d = client.get("/api/analysis/sale/3000002", headers=h).json()
    assert d["summary"]["n"] == 20 and d["summary"]["below_fair_pct"] >= 80   # sold ~40% under: mostly below fair
    assert all(r["verdict"] in ("bargain", "below", "above", "premium") for r in d["lots"])
    assert d["categories"]["model_group"][0]["key"] == "INTL_LT625"
    first = client.get("/api/analysis/sale/3000001", headers=h).json()
    assert first["summary"].get("n", 0) == 0                                  # oldest sale: nothing earlier to compare
    tr = client.get("/api/analysis/trends?include_low=true&min_n=2", headers=h).json()
    assert tr["summary"]["n"] >= 20 and tr["categories"]["location"]
