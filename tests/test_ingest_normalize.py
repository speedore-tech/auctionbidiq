from datetime import date

from bidiq.ingest.results_pdf import bucket_line, parse_results_pdf
from bidiq.ingest.sheets import parse_mileage_rows
from bidiq.ingest.tm_formats import (detect_pdf_kind, parse_catalog_pdf, parse_catalog_text,
                                     parse_readings_pdf)
from bidiq.normalize import body_class, engine_family, enrich, model_group, super_group, transmission
from scripts.make_sample_pdf import SAMPLE_ROWS, render

from .tm_fixtures import CATALOG_TEXT, render_catalog_pdf, render_mileage_pdf, render_reefer_pdf


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
    assert r["106"]["non_operable"]
    # TYPE column is the lot prefix: trailer T-101 does not collide with truck lot 101
    t = r["T-101"]
    assert t["lot_type"] == "T" and t["make"] == "UTILI" and t["class_raw"] == "REEFR" and t["sold_price"] == 21_000
    from bidiq.ingest import _result_row_fixups
    _result_row_fixups(t)
    e = enrich(dict(t))
    assert e["make"] == "UTIL" and e["model_group"] == "UTIL_REEFER" and e["body_class"] == "REEFER_TRAILER"
    assert e["hours"] == 19_806 and e["miles"] is None and e["door_type"] == "swing"
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
    assert rec["make"] == "INTL" and not rec.get("non_operable")   # P is a lot prefix, not "parts"
    assert body_class("VOLVO", "VOLVO_VNR", "CONV", None) == "TRACTOR_DAYCAB"
    from bidiq.normalize import norm_make
    assert [norm_make(m) for m in ("WABAS", "UTILI", "VANG0", "HYNDA", "STOUG", "GREAT", "WESST", "UTILITY", "GREAT DANE")] ==         ["WABASH", "UTIL", "VANGUARD", "HYUNDAI", "STOUGHTON", "GDAN", "WSTR", "UTIL", "GDAN"]
    assert rec["age_years"] == 6.5 and rec["miles_per_year"] == round(120_000 / 6.5)


def test_mileage_csv_flags_tamper_and_thousands():
    rows = parse_mileage_rows([
        {"lot": "101", "odometer": "186", "ecm_miles": "187,500"},
        {"lot": "102", "odometer": "221", "ecm_miles": "410,000"},
        {"lot": "T-401", "total_hours": "14,200", "engine_hours": "9,800"},
    ])
    by = {r["lot_no"]: r for r in rows}
    assert by["101"]["odometer_miles"] == 186_000 and not by["101"]["tamper_flag"]
    assert by["102"]["tamper_flag"]
    assert by["T-401"]["reefer_total_hours"] == 14200


def test_tm_catalog_lots_groups_and_footer():
    cat = parse_catalog_text(CATALOG_TEXT)
    assert cat.sale_date == date(2026, 10, 15) and cat.location == "Pekin, IL"
    lots = {l["lot_no"]: l for l in cat.lots}
    assert set(lots) == {"2", "8", "9", "10", "43", "M-4", "T-29", "T-30", "T-90"}
    assert cat.blank_lots == ["328"]
    p = lots["2"]
    assert (p["year"], p["make"], p["model_raw"]) == (2024, "PETERBILT", "579 UltraCab")
    assert p["engine_raw"] == "Cummins X15 Engine" and p["body_class"] == "TRACTOR_SLEEPER" and p["sleeper_size_in"] == 72
    # matched group: shared description applied to every lot number
    for lot in ("8", "9", "10"):
        assert lots[lot]["model_raw"] == "Cascadia126" and lots[lot]["engine_raw"] == "Detroit DD15 Engine"
        assert lots[lot]["trans_raw"] == "DT12 Transmission (Automatic)"
    assert lots["43"]["body_class"] == "TRACTOR_DAYCAB" and "Engine Brake" in lots["43"]["condition_notes"]
    m4 = lots["M-4"]
    assert m4["body_class"] == "BOX_TRUCK" and m4["body_length_ft"] == 26 and m4["liftgate"] and m4["rear_axle_lbs"] == 19000
    assert lots["T-29"]["body_class"] == "REEFER_TRAILER" and lots["T-29"]["engine_raw"].startswith("Thermo King")
    assert lots["T-90"]["door_type"] == "swing"
    e = {l: enrich(dict(lots[l])) for l in lots}
    assert e["8"]["model_group"] == "FRTL_CASCADIA" and e["8"]["engine_family"] == "DETROIT_DD15"
    assert e["43"]["engine_family"] == "VOLVO_D11"
    assert e["M-4"]["model_group"] == "FRTL_M2_106" and e["M-4"]["engine_family"] == "CUMMINS_B67"
    assert e["T-29"]["model_group"] == "GDAN_REEFER" and e["T-90"]["model_group"] == "UTIL_REEFER"
    assert super_group("GDAN_REEFER") == super_group("UTIL_REEFER") == "REEFER_53"


def test_tm_catalog_pdf_and_detection(tmp_path):
    p = tmp_path / "cat.pdf"
    render_catalog_pdf(p)
    import pdfplumber
    with pdfplumber.open(p) as pdf:
        assert detect_pdf_kind(pdf) == "catalog"
    assert len(parse_catalog_pdf(p).lots) == 9


def test_tm_mileage_grid(tmp_path):
    p = tmp_path / "mileage.pdf"
    render_mileage_pdf(p)
    import pdfplumber
    with pdfplumber.open(p) as pdf:
        assert detect_pdf_kind(pdf) == "mileage"
    r = parse_readings_pdf(p)
    assert r.kind == "mileage" and r.sale_date == date(2026, 10, 15) and r.location == "Pekin, IL"
    by = {x["lot_no"]: x for x in r.rows}
    assert by["1"]["status"] == "scratch"
    assert (by["2"]["odometer_miles"], by["2"]["ecm_miles"]) == (270_000, 258_000) and not by["2"]["tamper_flag"]
    assert by["52"]["status"] == "non_runner"
    assert by["160"]["odometer_miles"] == 1_012_000          # "1+012" = 1,012 thousand
    assert by["210"]["ecm_miles"] == 662 and by["210"]["tamper_flag"]   # "662 mi" = raw miles
    assert by["62"]["tamper_flag"]
    assert by["9"]["odometer_miles"] == 475_000 and by["9"].get("ecm_miles") is None
    assert by["M-12"]["meter_hours"] == 21960
    assert by["M-4"]["odometer_miles"] == 194_000


def test_tm_reefer_grid(tmp_path):
    p = tmp_path / "reefer.pdf"
    render_reefer_pdf(p)
    r = parse_readings_pdf(p)
    assert r.kind == "reefer" and r.sale_date == date(2026, 10, 15)
    by = {x["lot_no"]: x for x in r.rows}
    assert by["T-29"]["reefer_total_hours"] == 18189 and by["T-90"]["reefer_total_hours"] == 25345
    assert by["T-30"]["status"] == "non_runner" and by["T-91"]["status"] == "scratch"
