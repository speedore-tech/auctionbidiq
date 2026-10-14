from datetime import date

from bidiq.ingest.results_pdf import bucket_line, parse_results_pdf
from bidiq.ingest.sheets import catalog_from_text, parse_mileage_rows
from bidiq.normalize import body_class, engine_family, enrich, model_group, super_group, transmission
from scripts.make_sample_pdf import SAMPLE_ROWS, render


def test_parse_generated_tm_sheet(tmp_path):
    p = tmp_path / "r.pdf"
    render(p, "SALE: 2026055 05/14/2026 SOUTH SIOUX CITY", SAMPLE_ROWS)
    sheet = parse_results_pdf(p)
    assert sheet.sale_id == "2026055"
    assert sheet.sale_date == date(2026, 5, 14)
    assert sheet.location == "SOUTH SIOUX CITY"
    assert len(sheet.rows) == len(SAMPLE_ROWS) and not sheet.skipped
    r = {x["lot_no"]: x for x in sheet.rows}
    assert r["101"]["model_raw"] == "M2 106 BUSINESS CLAS"
    assert r["101"]["engine_raw"] == "CUMMINS ISB 6.7 LIT"
    assert r["101"]["trans_raw"] == "ALLISON 2500"
    assert r["101"]["miles"] == 186_420 and r["101"]["sold_price"] == 41_500
    assert r["104"]["desc_raw"] == "73IN RAISED SLP" and r["104"]["trans_raw"] == "10 SPD"
    assert r["106"]["non_operable"] and r["106"]["lot_type"] == "P"
    assert r["107"]["branded_title"] and not r["107"]["non_operable"]


def test_trans_miles_overlap_does_not_bleed():
    w = lambda t, x: {"text": t, "x0": x, "top": 100}
    line = [w("M", 10), w("101", 33), w("2019", 63), w("FRTL", 92), w("M2", 162), w("106", 175),
            w("ALLISON", 560), w("2500", 605), w("186,420", 630), w("41,500.00", 722)]
    row = bucket_line(line)
    assert row["trans_raw"] == "ALLISON 2500"
    assert row["miles"] == 186_420


def test_model_groups():
    # Business Class is the family name: same group as plain M2 106
    assert model_group("FRTL", "M2 106") == model_group("FRTL", "M2 106 BUSINESS CLAS") == "FRTL_M2_106"
    assert model_group("FRTL", "M2 112") == "FRTL_M2_112"
    assert model_group("INTER", "MV607") == "INTL_MV607"
    assert model_group("INTER", "4300") == model_group("INTL", "DURASTAR") == "INTL_4300"
    assert super_group("FRTL_M2_106") == super_group("INTL_MV607") == super_group("INTL_4300") == "MD_BOX"
    assert super_group("FRTL_M2_112") != "MD_BOX"


def test_engine_and_trans_families():
    for raw in ("CUMMINS ISB 6.7 LIT", "CUMMINS 6.7 LITER", "CUMMINS B6.7"):
        assert engine_family(raw) == "CUMMINS_B67"
    assert engine_family("NAVISTAR A26") == "NAVISTAR_A26"
    assert engine_family("CUMMINS X15") == "CUMMINS_X15"
    assert engine_family("DETROIT DD8") == "DETROIT_DD8"
    assert transmission("ALLISON 2500") == "ALLISON_AUTO"
    assert transmission("10 SPD") == "MANUAL_10SPD"


def test_body_class_and_enrich():
    assert body_class("INTER", "INTL_LT625", "CONV", "73IN RAISED SLP") == "TRACTOR_SLEEPER"
    assert body_class("FRTL", "FRTL_M2_106", "TRUCK", None) == "BOX_TRUCK"
    assert body_class("UTIL", "UTIL_3000R", "REEF TRL", "53FT REEFER") == "REEFER_TRAILER"
    rec = enrich({"make": "INTER", "model_raw": "MV607", "year": 2020, "miles": 120_000, "lot_type": "P",
                  "sale_date": date(2026, 5, 1)})
    assert rec["make"] == "INTL" and rec["non_operable"] is True
    assert rec["age_years"] == 6.5 and rec["miles_per_year"] == round(120_000 / 6.5)


def test_mileage_sheet_flags_tamper_and_thousands():
    rows = parse_mileage_rows([
        {"lot": "101", "odometer": "186", "ecm_miles": "187,500"},
        {"lot": "102", "odometer": "221", "ecm_miles": "410,000"},
        {"lot": "401", "total_hours": "14,200", "engine_hours": "9,800"},
    ])
    by = {r["lot_no"]: r for r in rows}
    assert by["101"]["odometer_miles"] == 186_000 and not by["101"]["tamper_flag"]
    assert by["102"]["tamper_flag"]
    assert by["401"]["reefer_engine_hours"] == 9800 and by["401"]["reefer_total_hours"] == 14200


def test_catalog_text_extraction():
    text = """LOT 210 2019 FRTL M2 106 BUSINESS CLASS
    CUMMINS ISB 6.7, ALLISON 2500 AUTO, 26' BOX W/ LIFTGATE
    GVWR 25,999  REAR AXLE 17,500  VIN 3ALACWFC5KDKE8613  192,000 MILES
    305 2020 INTER LT625 73" RAISED ROOF SLEEPER, A26, APU, JAKE BRAKE, 470,000 MILES"""
    recs = {r["lot_no"]: r for r in catalog_from_text(text)}
    a = recs["210"]
    assert a["gvwr"] == 25_999 and a["rear_axle_lbs"] == 17_500 and a["vin"] == "3ALACWFC5KDKE8613"
    assert a["body_length_ft"] == 26 and a["liftgate"] and a["miles"] == 192_000
    b = recs["305"]
    assert b["apu"] and b["engine_brake"] and b["miles"] == 470_000 and b["sleeper_size_in"] == 73
