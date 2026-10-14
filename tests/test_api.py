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
    assert r.status_code == 200 and r.json()["rows"] == 7
    r2 = up(a, "private")  # idempotent re-ingest
    assert r2.json()["replaced"] == 7
    assert len(client.get("/api/comps", headers=a).json()) == 7
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
