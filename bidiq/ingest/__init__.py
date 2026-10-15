"""Ingestion services: parsed rows -> normalized, enriched DB rows (idempotent)."""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..geo import lookup_location
from ..models import IngestLog, LotReading, Sale, SoldComp, TargetLot
from ..normalize import enrich, normalize_year
from .results_pdf import ParsedSheet
from .sheets import _bool, _num
from .tm_formats import location_key, norm_lot

def fit_lengths(model, rec: dict) -> dict:
    """Truncate strings to their VARCHAR length (Postgres enforces it; messy PDFs/CSVs can overflow)."""
    out = dict(rec)
    for col in model.__table__.columns:
        n = getattr(col.type, "length", None)
        v = out.get(col.name)
        if n and isinstance(v, str) and len(v) > n:
            out[col.name] = v[:n]
    return out


SPEC_FIELDS = {c.name for c in SoldComp.__table__.columns} - {"id", "tenant_id", "ingested_at"}
TARGET_FIELDS = {c.name for c in TargetLot.__table__.columns} - {"id", "tenant_id", "created_at", "updated_at"}


def ensure_sale(db: Session, sale_id: str, sale_date: date | None = None, location: str | None = None,
                upcoming: bool = False) -> Sale:
    sale = db.scalar(select(Sale).where(Sale.sale_id == sale_id))
    if not sale:
        sale = Sale(sale_id=sale_id, is_upcoming=upcoming)
        db.add(sale)
    if sale_date and not sale.sale_date:
        sale.sale_date = sale_date
    if location and not sale.location:
        sale.location = location
    if sale.location and sale.origin_lat is None:
        loc = lookup_location(sale.location)
        if loc:
            sale.origin_lat, sale.origin_lon = loc[0], loc[1]
    db.flush()
    return sale


def log(db: Session, tenant_id, kind, filename, sale_id, rows, status="ok", message=None) -> IngestLog:
    entry = IngestLog(tenant_id=tenant_id, kind=kind, filename=filename, sale_id=sale_id, rows=rows,
                      status=status, message=message)
    db.add(entry)
    return entry


def _result_row_fixups(rec: dict) -> None:
    """Trailer rows on T&M result sheets: MILES/HOURS holds reefer-unit hours (reefers) or nothing useful."""
    from ..normalize import TRAILER_CLASSES, clean

    cls = clean(rec.get("class_raw"))
    if cls in TRAILER_CLASSES:
        text = " ".join(clean(rec.get(k)) for k in ("model_raw", "engine_raw", "desc_raw", "trans_raw"))
        if rec.get("miles") is not None:
            if TRAILER_CLASSES[cls] == "REEFER" and rec.get("hours") is None:
                rec["hours"], rec["hours_metric"] = float(rec["miles"]), "sheet"
            rec["miles"] = None
        if not rec.get("door_type"):
            if re.search(r"SWING", text):
                rec["door_type"] = "swing"
            elif re.search(r"ROLL", text):
                rec["door_type"] = "rollup"
        if m := re.search(r"\b(\d{2})\s*FT", text):
            rec.setdefault("body_length_ft", float(m.group(1)))
        # trailer description text spills into the ENGINE column; only a reefer unit is an "engine"
        unit = clean(rec.get("engine_raw"))
        if not re.search(r"THERMO|CARRIER|\bTK\b|PRECEDENT|VECTOR|\bX4\b|\bS-?\d{3}|UNIT", unit):
            if rec.get("engine_raw"):
                rec["desc_raw"] = " ".join(x for x in (rec.get("engine_raw"), rec.get("desc_raw")) if x)[:120]
            rec["engine_raw"] = None


def ingest_results(db: Session, parsed: ParsedSheet, tenant_id: int | None, source_file: str,
                   sale_id_override: str | None = None) -> dict:
    """Upsert a parsed result sheet. Re-ingesting replaces the sale's rows (keyed sale_id+lot_no)."""
    sale_id = sale_id_override or parsed.sale_id
    if not sale_id:
        raise ValueError("Could not find 'SALE: <id> <date> <location>' header; pass sale_id explicitly.")
    sale = ensure_sale(db, sale_id, parsed.sale_date, parsed.location)
    existing = db.scalars(
        select(SoldComp).where(SoldComp.sale_id == sale_id,
                               SoldComp.tenant_id.is_(None) if tenant_id is None else SoldComp.tenant_id == tenant_id)
    ).all()
    prior_usage = {c.lot_no: c for c in existing}  # keep joined mileage/hours on re-ingest
    for c in existing:
        db.delete(c)
    db.flush()
    seen = set()
    n = 0
    for raw in parsed.rows:
        if raw["lot_no"] in seen:
            continue
        seen.add(raw["lot_no"])
        rec = dict(raw, sale_id=sale_id, sale_date=sale.sale_date, location=sale.location, source_file=source_file)
        _result_row_fixups(rec)
        enrich(rec)
        rec = fit_lengths(SoldComp, rec)
        comp = SoldComp(tenant_id=tenant_id, **{k: v for k, v in rec.items() if k in SPEC_FIELDS or k == "sold_price"})
        old = prior_usage.get(raw["lot_no"])
        if old:
            for k in ("odometer_miles", "ecm_miles", "tamper_flag", "hours", "hours_metric", "reefer_total_hours",
                      "reefer_engine_hours", "reefer_electric_hours"):
                if getattr(old, k) is not None and getattr(comp, k) in (None, False):
                    setattr(comp, k, getattr(old, k))
        db.add(comp)
        n += 1
    msg = f"{n} lots; replaced {len(existing)}"
    if parsed.skipped:
        msg += f"; {len(parsed.skipped)} unparsed lines: " + " | ".join(parsed.skipped[:5])
    log(db, tenant_id, "results", source_file, sale_id, n, "ok" if not parsed.skipped else "warn", msg)
    db.commit()
    return {"sale_id": sale_id, "sale_date": str(sale.sale_date) if sale.sale_date else None,
            "location": sale.location, "rows": n, "replaced": len(existing), "skipped_lines": parsed.skipped}


def _parse_date(s) -> date | None:
    if not s:
        return None
    if isinstance(s, date):
        return s
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            pass
    return None


def ingest_comps_csv(db: Session, rows: list[dict], tenant_id: int | None, source_file: str) -> dict:
    """Bulk import historical comps from CSV (e.g. an existing parsed dataset). Idempotent per sale+lot."""
    by_sale: dict[str, list[dict]] = {}
    for r in rows:
        sid = (r.get("sale_id") or r.get("sale") or "").strip()
        price = _num(r.get("sold_price") or r.get("price") or r.get("hammer"))
        lot = (r.get("lot_no") or r.get("lot") or "").strip().upper()
        if not sid or price is None or not lot:
            continue
        by_sale.setdefault(sid, []).append({
            "lot_no": lot,
            "lot_type": r.get("lot_type") or r.get("type") or None,
            "year": normalize_year(r.get("year")),
            "make": r.get("make") or None,
            "class_raw": r.get("class") or r.get("class_raw") or None,
            "model_raw": r.get("model") or r.get("model_raw") or None,
            "engine_raw": r.get("engine") or r.get("engine_raw") or None,
            "desc_raw": r.get("desc") or r.get("desc_raw") or r.get("description") or None,
            "trans_raw": r.get("trans") or r.get("trans_raw") or r.get("transmission") or None,
            "miles": int(_num(r.get("miles"))) if _num(r.get("miles")) is not None else None,
            "hours": _num(r.get("hours")),
            "hours_metric": r.get("hours_metric") or None,
            "door_type": r.get("door_type") or None,
            "body_length_ft": _num(r.get("body_length_ft")),
            "body_class": r.get("body_class") or None,
            "branded_title": bool(_bool(r.get("branded") or r.get("branded_title"))),
            "non_operable": bool(_bool(r.get("non_op") or r.get("non_operable"))),
            "sold_price": price,
            "_date": _parse_date(r.get("sale_date") or r.get("date")),
            "_loc": r.get("location") or None,
        })
    total = 0
    for sid, recs in by_sale.items():
        parsed = ParsedSheet(sale_id=sid, sale_date=recs[0]["_date"], location=recs[0]["_loc"])
        for rec in recs:
            rec.pop("_date"), rec.pop("_loc")
            parsed.rows.append({k: v for k, v in rec.items()})
        res = ingest_results(db, parsed, tenant_id, source_file)
        # carry hours/door/etc. that result PDFs don't have
        total += res["rows"]
    return {"sales": len(by_sale), "rows": total}


def _placeholder_id(location: str | None, d: date | None) -> str | None:
    key = location_key(location)
    return f"{key}-{d:%Y%m%d}" if key and d else None


def _is_placeholder(sale_id: str | None) -> bool:
    return bool(sale_id and re.fullmatch(r"[A-Z]+-\d{8}", sale_id))


def resolve_sale_id(db: Session, tenant_id: int, sale_id: str | None, sale_date: date | None,
                    location: str | None) -> str | None:
    """Typed/filename id wins. Otherwise reuse the id this sale already has (from an earlier catalog,
    sheet or sale record with the same date + location), else make 'PEKIN-20261015'."""
    if sale_id:
        return sale_id.strip()
    if not sale_date:
        return None
    key = location_key(location)
    official = db.scalar(select(LotReading.sale_id).where(
        LotReading.tenant_id == tenant_id, LotReading.sale_date == sale_date, LotReading.location_key == key,
        LotReading.sale_id.is_not(None)).limit(1))
    if official:
        return official
    for s in db.scalars(select(Sale).where(Sale.sale_date == sale_date)):
        if key is None or location_key(s.location) == key:
            return s.sale_id
    return _placeholder_id(location, sale_date)


def _same_sale_targets(db: Session, tenant_id: int, sale_id: str | None, sale_date: date | None,
                       location: str | None) -> list[TargetLot]:
    """Targets of this sale: same sale id, or same sale date (+ location when known)."""
    conds = []
    if sale_id:
        conds.append(TargetLot.sale_id == sale_id)
    if sale_date:
        conds.append(TargetLot.sale_date == sale_date)
    if not conds:
        return []
    key = location_key(location)
    out = []
    for t in db.scalars(select(TargetLot).where(TargetLot.tenant_id == tenant_id, or_(*conds))):
        if t.sale_id == sale_id or key is None or location_key(t.location) in (None, key):
            out.append(t)
    return out


def ingest_catalog(db: Session, recs: list[dict], tenant_id: int, sale_id: str | None, source_file: str,
                   sale_date: date | None = None, location: str | None = None) -> dict:
    sale_id = resolve_sale_id(db, tenant_id, sale_id, sale_date, location)
    if not sale_id:
        raise ValueError("Couldn't tell which sale this catalog is for — enter a Sale ID (or the sale date + location).")
    # The same sale already loaded under another id (e.g. "20261015" typed once, "2026062" from a filename)?
    # Update that set of lots instead of creating a duplicate set.
    merged_into = None
    if sale_date:
        key = location_key(location)
        other = {t.sale_id for t in db.scalars(select(TargetLot).where(
            TargetLot.tenant_id == tenant_id, TargetLot.sale_date == sale_date, TargetLot.sale_id != sale_id))
            if key is None or location_key(t.location) in (None, key)}
        if len(other) == 1:
            merged_into = sale_id
            sale_id = other.pop()
    sale = ensure_sale(db, sale_id, sale_date, location, upcoming=True)
    existing = {t.lot_no: t for t in db.scalars(select(TargetLot).where(
        TargetLot.tenant_id == tenant_id, TargetLot.sale_id == sale_id))}
    created = updated = 0
    for rec in recs:
        rec = dict(rec, lot_no=norm_lot(rec.get("lot_no")), sale_id=sale_id, sale_date=sale.sale_date,
                   location=sale.location, source_file=source_file)
        if not rec["lot_no"]:
            continue
        rec = fit_lengths(TargetLot, enrich(rec))
        tl = existing.get(rec["lot_no"])
        if not tl:
            tl = TargetLot(tenant_id=tenant_id, checklist={}, photos=[])
            db.add(tl)
            existing[rec["lot_no"]] = tl
            created += 1
        else:
            updated += 1
        for k, v in rec.items():
            if k in TARGET_FIELDS and v is not None:
                setattr(tl, k, v)
    db.flush()
    applied = _apply_stored_readings(db, tenant_id, list(existing.values()), sale.sale_date, sale.location)
    msg = f"{created} new / {updated} updated target lots"
    if merged_into:
        msg += f" (this sale was already loaded as {sale_id}; updated those lots instead of duplicating under {merged_into})"
    if applied:
        msg += f"; mileage/hours applied to {applied} lots from sheets already uploaded"
    log(db, tenant_id, "catalog", source_file, sale_id, created + updated, "ok", msg)
    db.commit()
    return {"sale_id": sale_id, "sale_date": str(sale.sale_date) if sale.sale_date else None,
            "location": sale.location, "created": created, "updated": updated, "readings_applied": applied}


def apply_reading(t: TargetLot, r: LotReading) -> None:
    for k in ("odometer_miles", "ecm_miles"):
        if getattr(r, k) is not None:
            setattr(t, k, getattr(r, k))
    t.tamper_flag = bool(r.tamper_flag)
    reads = [v for v in (r.odometer_miles, r.ecm_miles) if v]
    if reads:
        t.miles = max(reads)  # conservative: price on the higher meter reading
    if r.meter_hours is not None:
        t.hours, t.hours_metric = r.meter_hours, "meter"
    if r.reefer_hours is not None:
        # The T&M reefer sheet doesn't say Total vs Engine — keep it labelled as such.
        t.reefer_total_hours, t.hours, t.hours_metric = r.reefer_hours, r.reefer_hours, "sheet"
    if r.status == "scratch":
        t.status = "scratched"
    elif r.status == "non_runner":
        t.non_operable = True
    if r.note:
        tag = f"[Sheet: {r.note}]"
        if tag not in (t.condition_notes or ""):
            t.condition_notes = f"{tag} {t.condition_notes or ''}".strip()


def _apply_stored_readings(db: Session, tenant_id: int, targets: list[TargetLot], sale_date: date | None,
                           location: str | None) -> int:
    if not sale_date or not targets:
        return 0
    key = location_key(location)
    rows = db.scalars(select(LotReading).where(LotReading.tenant_id == tenant_id, LotReading.sale_date == sale_date))
    by_lot = {r.lot_no: r for r in rows if key is None or r.location_key in (None, key)}
    n = 0
    for t in targets:
        r = by_lot.get(t.lot_no)
        if r is not None:
            apply_reading(t, r)
            n += 1
    return n


def ingest_readings(db: Session, parsed, tenant_id: int, source_file: str, sale_id: str | None = None) -> dict:
    """Store mileage / reefer-hour readings and apply them to this sale's target lots (if loaded yet)."""
    sale_date, location = parsed.sale_date, parsed.location
    if not sale_date and not sale_id:
        raise ValueError("Couldn't find the sale date on this sheet — enter the Sale ID it belongs to.")
    key = location_key(location)
    existing = {(r.lot_no): r for r in db.scalars(select(LotReading).where(
        LotReading.tenant_id == tenant_id, LotReading.sale_date == sale_date, LotReading.location_key == key))} \
        if sale_date else {}
    stored = []
    for row in parsed.rows:
        lot = norm_lot(row.get("lot_no"))
        if not lot:
            continue
        r = existing.get(lot)
        if r is None:
            r = LotReading(tenant_id=tenant_id, sale_date=sale_date, location_key=key, lot_no=lot)
            db.add(r)
            existing[lot] = r
        r.sale_id = sale_id or r.sale_id
        r.source_file = source_file
        for k in ("odometer_miles", "ecm_miles", "meter_hours", "status", "note"):
            if row.get(k) is not None:
                setattr(r, k, row[k] if not isinstance(row[k], str) else row[k][:120])
        if row.get("reefer_total_hours") is not None:
            r.reefer_hours = row["reefer_total_hours"]
        r.tamper_flag = bool(row.get("tamper_flag")) or bool(r.tamper_flag and row.get("odometer_miles") is None)
        stored.append(r)
    db.flush()

    targets = _same_sale_targets(db, tenant_id, sale_id, sale_date, location)
    # an official numeric sale id (e.g. 2026062 from the sheet's filename) replaces a placeholder id
    if sale_id and targets and not _is_placeholder(sale_id):
        for old in {t.sale_id for t in targets if _is_placeholder(t.sale_id)}:
            for t in targets:
                if t.sale_id == old:
                    t.sale_id = sale_id
            if not db.scalar(select(Sale).where(Sale.sale_id == sale_id)):
                s = db.scalar(select(Sale).where(Sale.sale_id == old))
                if s:
                    s.sale_id = sale_id
    by_lot = {t.lot_no: t for t in targets}
    matched = 0
    for r in stored:
        t = by_lot.get(r.lot_no)
        if t is not None:
            apply_reading(t, r)
            matched += 1
    scratched = sum(1 for r in stored if r.status == "scratch")
    nonrun = sum(1 for r in stored if r.status == "non_runner")
    tamper = sum(1 for r in stored if r.tamper_flag)
    kind = getattr(parsed, "kind", "mileage")
    msg = (f"{len(stored)} {'reefer-hour' if kind == 'reefer' else 'mileage'} readings"
           f" ({scratched} scratched, {nonrun} non-runners, {tamper} ODOM/ECM mismatches); ")
    msg += f"applied to {matched} target lots" if targets else \
        "saved — they'll be applied automatically when this sale's catalog is uploaded"
    log(db, tenant_id, kind, source_file, sale_id or (targets[0].sale_id if targets else None), len(stored), "ok", msg)
    db.commit()
    return {"kind": kind, "sale_id": sale_id or (targets[0].sale_id if targets else None),
            "sale_date": str(sale_date) if sale_date else None, "location": location, "rows": len(stored),
            "matched": matched, "tamper_flags": tamper, "scratched": scratched, "non_runners": nonrun,
            "message": msg}
