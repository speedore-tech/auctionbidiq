"""Ingestion services: parsed rows -> normalized, enriched DB rows (idempotent)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..geo import lookup_location
from ..models import IngestLog, Sale, SoldComp, TargetLot
from ..normalize import enrich, normalize_year
from .results_pdf import ParsedSheet
from .sheets import _bool, _num, apply_mileage

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


def ingest_catalog(db: Session, recs: list[dict], tenant_id: int, sale_id: str, source_file: str,
                   sale_date: date | None = None, location: str | None = None) -> dict:
    sale = ensure_sale(db, sale_id, sale_date, location, upcoming=True)
    created = updated = 0
    for rec in recs:
        rec = dict(rec, sale_id=sale_id, sale_date=sale.sale_date, location=sale.location, source_file=source_file)
        rec = fit_lengths(TargetLot, enrich(rec))
        tl = db.scalar(select(TargetLot).where(TargetLot.tenant_id == tenant_id, TargetLot.sale_id == sale_id,
                                               TargetLot.lot_no == rec["lot_no"]))
        if not tl:
            tl = TargetLot(tenant_id=tenant_id, checklist={}, photos=[])
            db.add(tl)
            created += 1
        else:
            updated += 1
        for k, v in rec.items():
            if k in TARGET_FIELDS and v is not None:
                setattr(tl, k, v)
    log(db, tenant_id, "catalog", source_file, sale_id, created + updated, "ok",
        f"{created} new / {updated} updated target lots")
    db.commit()
    return {"sale_id": sale_id, "created": created, "updated": updated}


def ingest_mileage(db: Session, rows: list[dict], tenant_id: int, sale_id: str, source_file: str) -> dict:
    comps = {c.lot_no: c for c in db.scalars(select(SoldComp).where(
        SoldComp.sale_id == sale_id, or_(SoldComp.tenant_id.is_(None), SoldComp.tenant_id == tenant_id)))}
    targets = {t.lot_no: t for t in db.scalars(select(TargetLot).where(
        TargetLot.sale_id == sale_id, TargetLot.tenant_id == tenant_id))}
    matched = tampered = 0
    for m in rows:
        hit = False
        for obj in (comps.get(m["lot_no"]), targets.get(m["lot_no"])):
            if obj is not None:
                apply_mileage(obj, m)
                hit = True
        matched += hit
        tampered += bool(m.get("tamper_flag"))
    log(db, tenant_id, "mileage", source_file, sale_id, matched, "ok",
        f"{matched}/{len(rows)} lots matched; {tampered} ODOM/ECM mismatches flagged")
    db.commit()
    return {"sale_id": sale_id, "rows": len(rows), "matched": matched, "tamper_flags": tampered}
