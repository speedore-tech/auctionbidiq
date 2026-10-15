"""FastAPI app: /api/* JSON endpoints + the static web UI at /."""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import Ctx, current_ctx, hash_password, issue_token, revoke_tokens, verify_password
from ..config import ALLOW_REGISTRATION, BASE_DIR, DEFAULT_SETTINGS, UPLOAD_DIR, merged_settings
from ..db import get_db, init_db
from ..fmv import TIERS, fmv_row_dict, validate_source
from ..geo import LOCATIONS
from ..ingest import TARGET_FIELDS, fit_lengths, ingest_catalog, ingest_comps_csv, ingest_readings, ingest_results
from ..ingest.results_pdf import parse_results_pdf
from ..ingest.sheets import catalog_from_rows, parse_mileage_rows, read_csv_rows
from ..ingest.tm_formats import (ParsedReadings, detect_csv_kind, detect_pdf_kind, location_key, parse_any_date,
                                 parse_readings_pdf)
from ..ingest.tm_formats import parse_catalog_pdf as parse_tm_catalog_pdf
from ..models import IngestLog, LotReading, Outcome, RetailFMV, Sale, SoldComp, TargetLot, Tenant, User
from ..normalize import BODY_CLASSES, ENGINE_RULES, MODEL_RULES, SUPER_GROUPS, enrich
from ..recommender import plan_multi_lot
from ..risk import cdl_check, vin_check, warranty_check
from ..services import (COMP_COLS, LOT_COLS, SALE_COLS, PricingCache, backtest, comp_scope, evaluate, load_comps, row_dict,
                        summary_row)

app = FastAPI(title="BidIQ — Auction Bid Intelligence", version="1.0.0")


@app.on_event("startup")
def _startup():
    init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _j(d: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in d.items()}


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.scalar(select(func.count()).select_from(Tenant))
    return {"ok": True, "service": "bidiq"}


# ================================================================== auth
class RegisterIn(BaseModel):
    tenant_name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if not ALLOW_REGISTRATION:
        raise HTTPException(403, "Sign-up is disabled on this server — ask the admin for an account.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(400, "Email already registered")
    t = Tenant(name=body.tenant_name, settings={})
    db.add(t)
    db.flush()
    u = User(tenant_id=t.id, email=body.email.lower(), password_hash=hash_password(body.password), role="admin")
    db.add(u)
    db.commit()
    return {"token": issue_token(db, u), "tenant": t.name, "email": u.email}


@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Bad email or password")
    t = db.get(Tenant, u.tenant_id)
    return {"token": issue_token(db, u), "tenant": t.name, "email": u.email}


@app.get("/api/auth/me")
def me(ctx: Ctx = Depends(current_ctx)):
    return {"email": ctx.user.email, "tenant": ctx.tenant.name, "tenant_id": ctx.tenant_id, "role": ctx.user.role,
            "is_admin": ctx.is_admin, "is_superadmin": ctx.is_superadmin}


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/password")
def change_password(body: PasswordIn, ctx: Ctx = Depends(current_ctx)):
    if not verify_password(body.current_password, ctx.user.password_hash):
        raise HTTPException(400, "Current password is wrong")
    _check_password(body.new_password)
    ctx.user.password_hash = hash_password(body.new_password)
    ctx.db.commit()
    return {"ok": True}


# ================================================================== user management
ROLES = ("admin", "member")


def _check_password(pw: str) -> None:
    if len(pw or "") < 8:
        raise HTTPException(400, "Password must be at least 8 characters")


def _user_row(u: User, tenants: dict) -> dict:
    return {"id": u.id, "email": u.email, "role": u.role, "tenant_id": u.tenant_id,
            "tenant": tenants.get(u.tenant_id), "created_at": u.created_at.isoformat() if u.created_at else None}


def _managed_user(ctx: Ctx, uid: int) -> User:
    ctx.require_admin()
    u = ctx.db.get(User, uid)
    if not u or (u.tenant_id != ctx.tenant_id and not ctx.is_superadmin):
        raise HTTPException(404, "User not found")
    return u


@app.get("/api/users")
def list_users(ctx: Ctx = Depends(current_ctx)):
    ctx.require_admin()
    st = select(User).order_by(User.tenant_id, User.email)
    if not ctx.is_superadmin:
        st = st.where(User.tenant_id == ctx.tenant_id)
    tenants = {t.id: t.name for t in ctx.db.scalars(select(Tenant))}
    return {"users": [_user_row(u, tenants) for u in ctx.db.scalars(st)],
            "workspaces": [{"id": i, "name": n} for i, n in sorted(tenants.items())] if ctx.is_superadmin
            else [{"id": ctx.tenant_id, "name": ctx.tenant.name}],
            "can_create_workspace": ctx.is_superadmin, "me": ctx.user.id}


class UserIn(BaseModel):
    email: str
    password: str
    role: str = "member"
    workspace: str = "mine"          # mine | new | <tenant id> (superadmin only for new/other)
    workspace_name: str | None = None


@app.post("/api/users")
def create_user(body: UserIn, ctx: Ctx = Depends(current_ctx)):
    ctx.require_admin()
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Enter a valid email")
    if body.role not in ROLES:
        raise HTTPException(400, f"Role must be one of {ROLES}")
    _check_password(body.password)
    if ctx.db.scalar(select(User).where(User.email == email)):
        raise HTTPException(400, "A login with that email already exists — reset its password instead")
    if body.workspace == "mine":
        tid = ctx.tenant_id
    elif not ctx.is_superadmin:
        raise HTTPException(403, "Only the platform owner can create users in other workspaces")
    elif body.workspace == "new":
        if not (body.workspace_name or "").strip():
            raise HTTPException(400, "Name the new workspace (e.g. the partner's company)")
        t = Tenant(name=body.workspace_name.strip(), settings={})
        ctx.db.add(t)
        ctx.db.flush()
        tid = t.id
    else:
        t = ctx.db.get(Tenant, int(body.workspace)) if body.workspace.isdigit() else None
        if not t:
            raise HTTPException(400, "Unknown workspace")
        tid = t.id
    u = User(tenant_id=tid, email=email, password_hash=hash_password(body.password), role=body.role)
    ctx.db.add(u)
    ctx.db.commit()
    tenants = {t.id: t.name for t in ctx.db.scalars(select(Tenant))}
    return _user_row(u, tenants)


class UserUpdate(BaseModel):
    password: str | None = None
    role: str | None = None


@app.put("/api/users/{uid}")
def update_user(uid: int, body: UserUpdate, ctx: Ctx = Depends(current_ctx)):
    u = _managed_user(ctx, uid)
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, f"Role must be one of {ROLES}")
        if u.id == ctx.user.id and body.role != "admin" and not ctx.is_superadmin:
            raise HTTPException(400, "You can't remove your own admin role")
        u.role = body.role
    if body.password:
        _check_password(body.password)
        u.password_hash = hash_password(body.password)
        if u.id != ctx.user.id:
            revoke_tokens(ctx.db, u.id)  # sign them out everywhere
    ctx.db.commit()
    return {"ok": True}


@app.delete("/api/users/{uid}")
def delete_user(uid: int, ctx: Ctx = Depends(current_ctx)):
    u = _managed_user(ctx, uid)
    if u.id == ctx.user.id:
        raise HTTPException(400, "You can't delete your own login")
    revoke_tokens(ctx.db, u.id)
    ctx.db.delete(u)
    ctx.db.commit()
    return {"deleted": uid}


# ================================================================== settings & meta
@app.get("/api/settings")
def get_settings(ctx: Ctx = Depends(current_ctx)):
    return {"settings": ctx.settings, "defaults": DEFAULT_SETTINGS}


@app.put("/api/settings")
def put_settings(body: dict, ctx: Ctx = Depends(current_ctx)):
    ctx.require_admin()
    cur = dict(ctx.tenant.settings or {})
    for k, v in body.items():
        if k in DEFAULT_SETTINGS:
            cur[k] = v
    ctx.tenant.settings = cur
    ctx.db.commit()
    return {"settings": merged_settings(cur)}


@app.get("/api/meta")
def meta(ctx: Ctx = Depends(current_ctx)):
    groups = sorted({g for _, _, g in MODEL_RULES})
    seen = ctx.db.execute(select(SoldComp.model_group, func.count()).where(comp_scope(ctx.tenant_id, ctx.settings))
                          .group_by(SoldComp.model_group)).all()
    return {
        "model_groups": sorted(set(groups) | {g for g, _ in seen if g}),
        "super_groups": SUPER_GROUPS,
        "engine_families": sorted({f for _, f, _ in ENGINE_RULES}),
        "body_classes": BODY_CLASSES,
        "locations": sorted(LOCATIONS),
        "fmv_tiers": TIERS,
    }


@app.get("/api/dashboard")
def dashboard(ctx: Ctx = Depends(current_ctx)):
    db = ctx.db
    scope = comp_scope(ctx.tenant_id, ctx.settings)
    n_comps = len(load_comps(db, ctx.tenant_id, ctx.settings))
    n_excl = db.scalar(select(func.count()).select_from(SoldComp).where(
        scope, or_(SoldComp.non_operable.is_(True), SoldComp.branded_title.is_(True))))
    coverage = db.execute(select(SoldComp.model_group, SoldComp.body_class, func.count(), func.min(SoldComp.year),
                                 func.max(SoldComp.year), func.avg(SoldComp.sold_price))
                          .where(scope, SoldComp.non_operable.is_(False), SoldComp.branded_title.is_(False))
                          .group_by(SoldComp.model_group, SoldComp.body_class)
                          .order_by(func.count().desc())).all()
    sales = db.execute(select(SoldComp.sale_id, SoldComp.sale_date, SoldComp.location, func.count())
                       .where(scope).group_by(SoldComp.sale_id, SoldComp.sale_date, SoldComp.location)
                       .order_by(SoldComp.sale_date.desc()).limit(15)).all()
    n_targets = db.scalar(select(func.count()).select_from(TargetLot).where(TargetLot.tenant_id == ctx.tenant_id))
    n_fmv = db.scalar(select(func.count()).select_from(RetailFMV).where(RetailFMV.tenant_id == ctx.tenant_id))
    upcoming = db.execute(select(Sale).where(Sale.is_upcoming.is_(True)).order_by(Sale.sale_date)).scalars().all()
    return {
        "comps": n_comps, "excluded": n_excl, "targets": n_targets, "fmv_rows": n_fmv,
        "coverage": [{"model_group": g, "body_class": b, "n": n, "year_min": y0, "year_max": y1,
                      "avg_price": round(a or 0)} for g, b, n, y0, y1, a in coverage],
        "recent_sales": [{"sale_id": s, "sale_date": d.isoformat() if d else None, "location": l, "lots": n}
                         for s, d, l, n in sales],
        "upcoming_sales": [_j(row_dict(s, SALE_COLS)) for s in upcoming],
    }


# ================================================================== ingest
def _save_upload(up: UploadFile, data: bytes) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", up.filename or "upload")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    p = UPLOAD_DIR / safe
    p.write_bytes(data)
    return safe


def _visibility_tenant(ctx: Ctx, visibility: str) -> int | None:
    if visibility == "shared":
        if not ctx.settings.get("contribute_to_shared", True):
            raise HTTPException(400, "This tenant has opted out of contributing to the shared comp pool.")
        return None
    return ctx.tenant_id


KIND_LABEL = {"results": "result sheet", "catalog": "catalog", "mileage": "mileage sheet", "reefer": "reefer-hour sheet"}


def _detect_kind(data: bytes, name: str) -> str | None:
    if name.lower().endswith(".csv"):
        rows = read_csv_rows(data)
        return detect_csv_kind(list(rows[0].keys()) if rows else [])
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return detect_pdf_kind(pdf)


def _ingest_file(ctx: Ctx, data: bytes, name: str, expected: str | None, *, visibility: str = "shared",
                 sale_id: str | None = None, sale_date: str | None = None, location: str | None = None) -> dict:
    """Detect what a T&M file is and ingest it accordingly — a file dropped in the wrong box still works."""
    sale_id = (sale_id or "").strip() or None
    location = (location or "").strip() or None
    sd = parse_any_date(sale_date)
    try:
        kind = _detect_kind(data, name) or expected
    except Exception:
        raise HTTPException(422, "Couldn't open this file — is it a valid PDF or CSV?")
    if not kind:
        raise HTTPException(422, "Couldn't tell what this file is (result sheet, catalog, mileage or reefer-hour sheet).")
    note = None
    if expected and kind != expected and not (expected == "mileage" and kind == "reefer"):
        note = f"This looks like a {KIND_LABEL[kind]}, not a {KIND_LABEL[expected]} — ingested it as a {KIND_LABEL[kind]}."
    tol = float(ctx.settings.get("odom_ecm_tolerance_pct", 10))
    try:
        if kind == "results":
            tid = _visibility_tenant(ctx, visibility)
            if name.lower().endswith(".csv"):
                res = ingest_comps_csv(ctx.db, read_csv_rows(data), tid, name)
            else:
                parsed = parse_results_pdf(io.BytesIO(data))
                if not parsed.rows:
                    raise HTTPException(422, "No lot rows found — is this a T&M 'PRICE LISTING BY TYPE/LOT#' sheet?")
                # an override would merge files into one sale, so it's only honoured when the header lacks one
                res = ingest_results(ctx.db, parsed, tid, name, sale_id_override=None if parsed.sale_id else sale_id)
        elif kind == "catalog":
            if name.lower().endswith(".csv"):
                recs, cat_date, cat_loc = catalog_from_rows(read_csv_rows(data)), None, None
            else:
                cat = parse_tm_catalog_pdf(io.BytesIO(data))
                recs, cat_date, cat_loc = cat.lots, cat.sale_date, cat.location
            if not recs:
                raise HTTPException(422, "No lots found in this catalog.")
            res = ingest_catalog(ctx.db, recs, ctx.tenant_id, sale_id, name, sd or cat_date, location or cat_loc)
        else:
            if name.lower().endswith(".csv"):
                parsed = ParsedReadings(kind="mileage", sale_date=sd, location=location,
                                        rows=parse_mileage_rows(read_csv_rows(data), tol))
            else:
                parsed = parse_readings_pdf(io.BytesIO(data), tol)
                parsed.sale_date = sd or parsed.sale_date
                parsed.location = location or parsed.location
            if not parsed.rows:
                raise HTTPException(422, "No lot readings found on this sheet.")
            res = ingest_readings(ctx.db, parsed, ctx.tenant_id, name, sale_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    res["kind"] = kind
    if note:
        res["note"] = note
    return res


@app.post("/api/ingest/auto")
async def ingest_auto_ep(file: UploadFile = File(...), visibility: str = Form("shared"),
                         sale_id: str | None = Form(None), sale_date: str | None = Form(None),
                         location: str | None = Form(None), ctx: Ctx = Depends(current_ctx)):
    data = await file.read()
    return _ingest_file(ctx, data, _save_upload(file, data), None, visibility=visibility, sale_id=sale_id,
                        sale_date=sale_date, location=location)


@app.post("/api/ingest/results")
async def ingest_results_ep(file: UploadFile = File(...), visibility: str = Form("shared"),
                            sale_id: str | None = Form(None), ctx: Ctx = Depends(current_ctx)):
    data = await file.read()
    return _ingest_file(ctx, data, _save_upload(file, data), "results", visibility=visibility, sale_id=sale_id)


@app.post("/api/ingest/catalog")
async def ingest_catalog_ep(file: UploadFile = File(...), sale_id: str | None = Form(None),
                            sale_date: str | None = Form(None), location: str | None = Form(None),
                            ctx: Ctx = Depends(current_ctx)):
    data = await file.read()
    return _ingest_file(ctx, data, _save_upload(file, data), "catalog", sale_id=sale_id, sale_date=sale_date,
                        location=location)


@app.post("/api/ingest/mileage")
async def ingest_mileage_ep(file: UploadFile = File(...), sale_id: str | None = Form(None),
                            sale_date: str | None = Form(None), ctx: Ctx = Depends(current_ctx)):
    data = await file.read()
    return _ingest_file(ctx, data, _save_upload(file, data), "mileage", sale_id=sale_id, sale_date=sale_date)


@app.get("/api/ingest/log")
def ingest_log(ctx: Ctx = Depends(current_ctx)):
    rows = ctx.db.scalars(select(IngestLog).where(or_(IngestLog.tenant_id.is_(None), IngestLog.tenant_id == ctx.tenant_id))
                          .order_by(IngestLog.id.desc()).limit(50))
    return [{"id": r.id, "kind": r.kind, "filename": r.filename, "sale_id": r.sale_id, "rows": r.rows,
             "status": r.status, "message": r.message, "created_at": r.created_at.isoformat(),
             "shared": r.tenant_id is None} for r in rows]


# ================================================================== manage data (delete & reload)
@app.get("/api/data/summary")
def data_summary(ctx: Ctx = Depends(current_ctx)):
    """What's loaded, per sale: historical comps (shared / mine) and upcoming-sale lots."""
    db = ctx.db
    comps: dict[str, dict] = {}
    for sid, sdate, loc, tid, n in db.execute(
            select(SoldComp.sale_id, SoldComp.sale_date, SoldComp.location, SoldComp.tenant_id, func.count())
            .where(comp_scope(ctx.tenant_id, ctx.settings))
            .group_by(SoldComp.sale_id, SoldComp.sale_date, SoldComp.location, SoldComp.tenant_id)):
        row = comps.setdefault(sid, {"sale_id": sid, "sale_date": sdate.isoformat() if sdate else None,
                                     "location": loc, "shared": 0, "private": 0})
        row["shared" if tid is None else "private"] += n
    for r in comps.values():
        r["duplicated"] = bool(r["shared"] and r["private"])
    lots = [{"sale_id": sid, "sale_date": d.isoformat() if d else None, "location": loc, "lots": n}
            for sid, d, loc, n in db.execute(
                select(TargetLot.sale_id, TargetLot.sale_date, TargetLot.location, func.count())
                .where(TargetLot.tenant_id == ctx.tenant_id)
                .group_by(TargetLot.sale_id, TargetLot.sale_date, TargetLot.location))]
    by_date: dict = {}
    for l in lots:
        by_date.setdefault((l["sale_date"], location_key(l["location"])), []).append(l["sale_id"])
    for l in lots:
        others = [s for s in by_date[(l["sale_date"], location_key(l["location"]))] if s != l["sale_id"]]
        l["same_sale_as"] = others if l["sale_date"] else []
    return {"comps": sorted(comps.values(), key=lambda r: r["sale_date"] or "", reverse=True),
            "lots": sorted(lots, key=lambda r: r["sale_date"] or "", reverse=True),
            "can_delete_shared": ctx.is_superadmin, "is_admin": ctx.is_admin}


@app.delete("/api/data/comps")
def delete_comps(scope: str, sale_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    """scope: 'private' (my workspace's comps), 'shared' (everyone's pool — platform owner only) or 'all'."""
    ctx.require_admin()
    if scope not in ("private", "shared", "all"):
        raise HTTPException(400, "scope must be private, shared or all")
    if scope in ("shared", "all") and not ctx.is_superadmin:
        raise HTTPException(403, "Only the platform owner can delete shared comps (they're used by every workspace).")
    conds = []
    if scope == "private":
        conds.append(SoldComp.tenant_id == ctx.tenant_id)
    elif scope == "shared":
        conds.append(SoldComp.tenant_id.is_(None))
    else:
        conds.append(or_(SoldComp.tenant_id.is_(None), SoldComp.tenant_id == ctx.tenant_id))
    if sale_id:
        conds.append(SoldComp.sale_id == sale_id)
    n = ctx.db.query(SoldComp).filter(*conds).delete(synchronize_session=False)
    ctx.db.add(IngestLog(tenant_id=None if scope != "private" else ctx.tenant_id, kind="delete", filename=None,
                         sale_id=sale_id, rows=n, status="ok",
                         message=f"{ctx.user.email} deleted {n} {scope} sold comps" + (f" for sale {sale_id}" if sale_id else " (all sales)")))
    ctx.db.commit()
    return {"deleted": n}


@app.delete("/api/data/lots")
def delete_lots(sale_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    """Delete this workspace's upcoming-sale lots (one sale, or all) and their stored meter readings."""
    ctx.require_admin()
    st = select(TargetLot).where(TargetLot.tenant_id == ctx.tenant_id)
    if sale_id:
        st = st.where(TargetLot.sale_id == sale_id)
    lots = ctx.db.scalars(st).all()
    ids = [t.id for t in lots]
    keys = {(t.sale_date, location_key(t.location)) for t in lots}
    if ids:  # outcomes keep their numbers but lose the link to the deleted lot
        ctx.db.query(Outcome).filter(Outcome.target_lot_id.in_(ids)).update({Outcome.target_lot_id: None},
                                                                           synchronize_session=False)
    n = ctx.db.query(TargetLot).filter(TargetLot.id.in_(ids)).delete(synchronize_session=False) if ids else 0
    rq = ctx.db.query(LotReading).filter(LotReading.tenant_id == ctx.tenant_id)
    if sale_id:
        still = {(t.sale_date, location_key(t.location)) for t in ctx.db.scalars(
            select(TargetLot).where(TargetLot.tenant_id == ctx.tenant_id))}
        gone = [k for k in keys if k not in still and k[0] is not None]
        r = 0
        for d, key in gone:
            r += rq.filter(LotReading.sale_date == d, LotReading.location_key == key).delete(synchronize_session=False)
        r += ctx.db.query(LotReading).filter(LotReading.tenant_id == ctx.tenant_id,
                                             LotReading.sale_id == sale_id).delete(synchronize_session=False)
    else:
        r = rq.delete(synchronize_session=False)
    ctx.db.add(IngestLog(tenant_id=ctx.tenant_id, kind="delete", sale_id=sale_id, rows=n, status="ok",
                         message=f"{ctx.user.email} deleted {n} upcoming-sale lots and {r} mileage/hour readings"
                                 + (f" for sale {sale_id}" if sale_id else " (all sales)")))
    ctx.db.commit()
    return {"deleted": n, "readings_deleted": r}


# ================================================================== comps
@app.get("/api/comps")
def list_comps(model_group: str | None = None, body_class: str | None = None, year_min: int | None = None,
               year_max: int | None = None, sale_id: str | None = None, q: str | None = None,
               include_excluded: bool = True, limit: int = 500, ctx: Ctx = Depends(current_ctx)):
    st = select(SoldComp).where(comp_scope(ctx.tenant_id, ctx.settings))
    if model_group:
        st = st.where(SoldComp.model_group == model_group)
    if body_class:
        st = st.where(SoldComp.body_class == body_class)
    if year_min:
        st = st.where(SoldComp.year >= year_min)
    if year_max:
        st = st.where(SoldComp.year <= year_max)
    if sale_id:
        st = st.where(SoldComp.sale_id == sale_id)
    if q:
        like = f"%{q.upper()}%"
        st = st.where(or_(SoldComp.model_raw.like(like), SoldComp.engine_raw.like(like), SoldComp.make.like(like),
                          SoldComp.lot_no.like(like)))
    if not include_excluded:
        st = st.where(SoldComp.non_operable.is_(False), SoldComp.branded_title.is_(False))
    rows = ctx.db.scalars(st.order_by(SoldComp.sale_date.desc(), SoldComp.lot_no).limit(min(limit, 5000)))
    return [_j({**row_dict(c, COMP_COLS), "shared": c.tenant_id is None}) for c in rows]


@app.delete("/api/comps/{comp_id}")
def delete_comp(comp_id: int, ctx: Ctx = Depends(current_ctx)):
    c = ctx.db.get(SoldComp, comp_id)
    if not c or c.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "Not found (shared comps cannot be deleted by a tenant)")
    ctx.db.delete(c)
    ctx.db.commit()
    return {"deleted": comp_id}


# ================================================================== sales
class SaleIn(BaseModel):
    sale_id: str
    sale_date: date | None = None
    location: str | None = None
    auction_house: str | None = None
    buyers_premium_pct: float | None = None
    origin_lat: float | None = None
    origin_lon: float | None = None
    deposit_amount: float | None = None
    wire_deadline: str | None = None
    payment_window_hours: int | None = None
    absolute: bool | None = None
    terms_notes: str | None = None
    is_upcoming: bool | None = None


@app.get("/api/sales")
def list_sales(ctx: Ctx = Depends(current_ctx)):
    return [_j(row_dict(s, SALE_COLS)) for s in ctx.db.scalars(select(Sale).order_by(Sale.sale_date.desc()))]


@app.post("/api/sales")
def upsert_sale(body: SaleIn, ctx: Ctx = Depends(current_ctx)):
    from ..geo import lookup_location
    s = ctx.db.scalar(select(Sale).where(Sale.sale_id == body.sale_id))
    if not s:
        s = Sale(sale_id=body.sale_id)
        ctx.db.add(s)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    if s.location and (s.origin_lat is None or "location" in body.model_fields_set and "origin_lat" not in body.model_fields_set):
        loc = lookup_location(s.location)
        if loc:
            s.origin_lat, s.origin_lon = loc[0], loc[1]
    ctx.db.commit()
    return _j(row_dict(s, SALE_COLS))


# ================================================================== predict / recommend / what-if
class EvalIn(BaseModel):
    spec: dict[str, Any] = {}
    overrides: dict[str, Any] = {}
    target_id: int | None = None


@app.post("/api/predict")
def predict_ep(body: EvalIn, ctx: Ctx = Depends(current_ctx)):
    ev = _eval(body, ctx)
    return {"spec": ev["spec"], "prediction": ev["prediction"], "fmv": ev["fmv"]}


@app.post("/api/recommend")
def recommend_ep(body: EvalIn, ctx: Ctx = Depends(current_ctx)):
    return _eval(body, ctx)


@app.post("/api/whatif")
def whatif_ep(body: EvalIn, ctx: Ctx = Depends(current_ctx)):
    return _eval(body, ctx)


def _eval(body: EvalIn, ctx: Ctx) -> dict:
    lot = dict(body.spec)
    if body.target_id:
        t = _get_target(ctx, body.target_id)
        lot = {**row_dict(t, LOT_COLS), **{k: v for k, v in body.spec.items() if v not in (None, "")}}
    if not (lot.get("model_group") or lot.get("model_raw")):
        raise HTTPException(400, "Need at least make + model (or a model_group)")
    return evaluate(ctx.db, ctx.tenant_id, ctx.settings, lot, body.overrides)


# ================================================================== auction-day analysis
@app.get("/api/analysis/sales")
def analysis_sales(ctx: Ctx = Depends(current_ctx)):
    """Past sales with result sheets loaded (the ones that can be analysed)."""
    comps = load_comps(ctx.db, ctx.tenant_id, ctx.settings)
    sales: dict[str, dict] = {}
    for c in comps:
        r = sales.setdefault(c["sale_id"], {"sale_id": c["sale_id"], "sale_date": c["sale_date"], "location": c["location"], "lots": 0})
        r["lots"] += 1
    out = sorted(sales.values(), key=lambda r: r["sale_date"] or date.min, reverse=True)
    return [_j(r) for r in out]


@app.get("/api/analysis/sale/{sale_id}")
def analysis_sale(sale_id: str, ctx: Ctx = Depends(current_ctx)):
    from ..analysis import GROUP_KEYS, analyze_sale, categories
    res = analyze_sale(load_comps(ctx.db, ctx.tenant_id, ctx.settings), sale_id, ctx.settings)
    res["categories"] = {k: categories(res["lots"], k, min_n=3) for k in GROUP_KEYS if k != "location"}
    return res


@app.get("/api/analysis/trends")
def analysis_trends(include_low: bool = False, min_n: int = 5, ctx: Ctx = Depends(current_ctx)):
    """Across every loaded sale: which kinds of units keep selling below / above fair value."""
    from ..analysis import GROUP_KEYS, analyze_all, categories, summarize
    rows = analyze_all(load_comps(ctx.db, ctx.tenant_id, ctx.settings), ctx.settings)
    use = rows if include_low else [r for r in rows if r["confidence"] != "Low"]
    return {"summary": summarize(use), "sales": len({r["sale_id"] for r in use}),
            "categories": {k: categories(use, k, min_n=max(2, min_n)) for k in GROUP_KEYS}}


# ================================================================== targets / bid sheet
def _get_target(ctx: Ctx, tid: int) -> TargetLot:
    t = ctx.db.get(TargetLot, tid)
    if not t or t.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "Target lot not found")
    return t


def _apply_target(t: TargetLot, body: dict):
    body = fit_lengths(TargetLot, body)
    for k, v in body.items():
        if k in TARGET_FIELDS and k not in ("last_recommendation",):
            if k == "sale_date" and isinstance(v, str):
                v = date.fromisoformat(v) if v else None
            if v == "" and k not in ("condition_notes",):
                v = None
            setattr(t, k, v)
    # re-normalize whenever raw spec changes
    rec = {k: getattr(t, k) for k in ("make", "model_raw", "class_raw", "desc_raw", "engine_raw", "trans_raw", "year",
                                      "miles", "hours", "sale_date", "lot_type")}
    for k in ("model_group", "engine_family", "transmission", "body_class"):
        rec[k] = body.get(k) or None
    enrich(rec)
    for k in ("make", "model_group", "super_group", "engine_family", "transmission", "body_class", "age_years",
              "miles_per_year", "sleeper"):
        if rec.get(k) is not None:
            setattr(t, k, rec[k])
    if t.year is not None:
        t.year = int(t.year)


@app.get("/api/targets")
def list_targets(sale_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    st = select(TargetLot).where(TargetLot.tenant_id == ctx.tenant_id)
    if sale_id:
        st = st.where(TargetLot.sale_id == sale_id)
    return [_j(row_dict(t, LOT_COLS)) for t in ctx.db.scalars(st.order_by(TargetLot.sale_id, TargetLot.run_order, TargetLot.lot_no))]


@app.post("/api/targets")
def create_target(body: dict, ctx: Ctx = Depends(current_ctx)):
    t = TargetLot(tenant_id=ctx.tenant_id, checklist={}, photos=[])
    _apply_target(t, body)
    if t.sale_id:
        from ..ingest import ensure_sale
        s = ensure_sale(ctx.db, t.sale_id, t.sale_date, t.location, upcoming=True)
        t.sale_date = t.sale_date or s.sale_date
        t.location = t.location or s.location
    ctx.db.add(t)
    ctx.db.commit()
    return _j(row_dict(t, LOT_COLS))


@app.put("/api/targets/{tid}")
def update_target(tid: int, body: dict, ctx: Ctx = Depends(current_ctx)):
    t = _get_target(ctx, tid)
    _apply_target(t, body)
    ctx.db.commit()
    return _j(row_dict(t, LOT_COLS))


@app.delete("/api/targets/{tid}")
def delete_target(tid: int, ctx: Ctx = Depends(current_ctx)):
    t = _get_target(ctx, tid)
    ctx.db.delete(t)
    ctx.db.commit()
    return {"deleted": tid}


@app.post("/api/targets/lock")
def lock_targets(sale_id: str, ctx: Ctx = Depends(current_ctx)):
    """Freeze the model's numbers for every lot of a sale (before the auction) for the scorecard."""
    from ..scorecard import lock_sale
    return lock_sale(ctx.db, ctx.tenant_id, ctx.settings, sale_id, ctx.user.email)


@app.get("/api/scorecard")
def get_scorecard(sale_id: str, ctx: Ctx = Depends(current_ctx)):
    """Locked bids vs actual hammers (after the sale's result sheet is uploaded)."""
    from ..scorecard import scorecard
    return scorecard(ctx.db, ctx.tenant_id, sale_id)


@app.get("/api/targets/{tid}/recommendation")
def target_recommendation(tid: int, ctx: Ctx = Depends(current_ctx)):
    t = _get_target(ctx, tid)
    ev = evaluate(ctx.db, ctx.tenant_id, ctx.settings, row_dict(t, LOT_COLS))
    rec = ev["recommendation"]
    if rec.get("ok") and not (t.last_recommendation or {}).get("locked"):  # never overwrite a locked bid
        t.last_recommendation = {"p50": ev["prediction"]["p50"], "hammer": rec["hammer"], "all_in": rec["all_in"],
                                 "confidence": ev["prediction"]["confidence"]["level"], "as_of": date.today().isoformat()}
        ctx.db.commit()
    return {"target": _j(row_dict(t, LOT_COLS)), **ev}


def _bidsheet_rows(ctx: Ctx, sale_id: str | None) -> list[dict]:
    st = select(TargetLot).where(TargetLot.tenant_id == ctx.tenant_id)
    if sale_id:
        st = st.where(TargetLot.sale_id == sale_id)
    cache = PricingCache(ctx.db, ctx.tenant_id, ctx.settings)
    from ..analysis import analyze_all, below_fair_rates
    hist = below_fair_rates(analyze_all(load_comps(ctx.db, ctx.tenant_id, ctx.settings), ctx.settings))
    out = []
    for t in ctx.db.scalars(st.order_by(TargetLot.run_order, TargetLot.lot_no)):
        lot = row_dict(t, LOT_COLS)
        ev = evaluate(ctx.db, ctx.tenant_id, ctx.settings, lot, cache=cache)
        row = summary_row(lot, ev)
        row["checklist"] = t.checklist or {}
        row["deposit"] = ev["recommendation"].get("deposit")
        row["sale_location"] = (ev.get("sale") or {}).get("location")
        row["history"] = hist.get(row.get("model_group"))   # how often this model sold below fair value
        row["locked_at"] = (t.last_recommendation or {}).get("locked_at") if (t.last_recommendation or {}).get("locked") else None
        out.append(row)
    return out


@app.get("/api/bidsheet")
def bidsheet(sale_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    rows = _bidsheet_rows(ctx, sale_id)
    sale = ctx.db.scalar(select(Sale).where(Sale.sale_id == sale_id)) if sale_id else None
    return {"sale": _j(row_dict(sale, SALE_COLS)) if sale else None, "rows": rows,
            "settings": {k: ctx.settings[k] for k in ("default_buyers_premium_pct", "home_label", "aggressiveness")}}


@app.get("/api/bidsheet.csv")
def bidsheet_csv(sale_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    rows = _bidsheet_rows(ctx, sale_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sale", "lot", "year", "make", "model", "group", "class", "engine", "miles", "hours", "gvwr", "cdl",
                "vin", "N", "confidence", "fair_p50", "target_hammer", "walk_hammer", "ceiling_hammer",
                "target_all_in", "walk_all_in", "ceiling_all_in", "win%_target", "win%_ceiling", "retail",
                "equity_at_target", "flags", "vin_sticker_match", "nicb", "nmvtis", "gvwr_confirmed"])
    for r in rows:
        h, a, p = r.get("hammer", {}), r.get("all_in", {}), r.get("win_prob", {})
        ck = r.get("checklist") or {}
        w.writerow([r["sale_id"], r["lot_no"], r["year"], r["make"], r["model_raw"], r["model_group"], r["body_class"],
                    r["engine_family"], r["miles"], r["hours"], r["gvwr"], r["cdl"], r["vin"], r.get("n"),
                    r.get("confidence"), r.get("p50"), h.get("target"), h.get("walk_away"), h.get("hard_ceiling"),
                    a.get("target"), a.get("walk_away"), a.get("hard_ceiling"),
                    round(p.get("target", 0) * 100) if p else "", round(p.get("hard_ceiling", 0) * 100) if p else "",
                    r.get("retail"), (r.get("equity_target") or {}).get("dollars"), " ".join(r["flags"]),
                    ck.get("vin_sticker_match", ""), ck.get("nicb", ""), ck.get("nmvtis", ""), ck.get("gvwr_confirmed", "")])
    buf.seek(0)
    fn = f"bidsheet_{sale_id or 'all'}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fn}"'})


class MultiLotIn(BaseModel):
    target_ids: list[int]
    budget_all_in: float
    max_wins: int | None = None
    deescalate_pct: float | None = None
    results: dict[str, dict] = {}


@app.post("/api/multilot/plan")
def multilot(body: MultiLotIn, ctx: Ctx = Depends(current_ctx)):
    comps = load_comps(ctx.db, ctx.tenant_id, ctx.settings)
    items = []
    for tid in body.target_ids:
        t = _get_target(ctx, tid)
        ev = evaluate(ctx.db, ctx.tenant_id, ctx.settings, row_dict(t, LOT_COLS), comps=comps)
        rec = ev["recommendation"]
        if not rec.get("ok"):
            continue
        cb = rec["cost_basis"]
        items.append({"id": t.id, "lot_no": t.lot_no, "run_order": t.run_order,
                      "label": f"{t.year or ''} {t.make or ''} {t.model_raw or ''}".strip(),
                      "premium_pct": cb["buyers_premium_pct"], "transport": cb["transport"]["cost"],
                      "fees": cb["title_doc_fees"], "target_all_in": rec["all_in"]["target"],
                      "ceiling_all_in": rec["all_in"]["hard_ceiling"]})
    return plan_multi_lot(items, body.budget_all_in,
                          body.max_wins if body.max_wins is not None else int(ctx.settings["multi_lot_max_wins"]),
                          body.deescalate_pct if body.deescalate_pct is not None else float(ctx.settings["multi_lot_deescalate_pct"]),
                          body.results)


# ================================================================== risk tools
class VinIn(BaseModel):
    vin: str
    year: int | None = None
    make: str | None = None


@app.post("/api/risk/vin")
def risk_vin(body: VinIn, ctx: Ctx = Depends(current_ctx)):
    from ..normalize import norm_make
    return vin_check(body.vin, body.year, norm_make(body.make))


class CdlIn(BaseModel):
    gvwr: int | None = None
    rear_axle_lbs: int | None = None
    body_class: str | None = None
    year: int | None = None
    miles: int | None = None


@app.post("/api/risk/cdl")
def risk_cdl(body: CdlIn, ctx: Ctx = Depends(current_ctx)):
    return {"cdl": cdl_check(body.gvwr, body.rear_axle_lbs, body.body_class),
            "warranty": warranty_check(body.year, body.miles, body.gvwr, body.body_class, ctx.settings)}


# ================================================================== retail FMV
class FmvIn(BaseModel):
    region: str | None = None
    model_group: str
    year_lo: int
    year_hi: int
    miles_lo: int | None = None
    miles_hi: int | None = None
    tier: str = "private"
    price: float
    is_asking: bool = True
    as_of_date: date | None = None
    source: str | None = None
    url: str | None = None
    notes: str | None = None


@app.get("/api/fmv")
def list_fmv(model_group: str | None = None, ctx: Ctx = Depends(current_ctx)):
    st = select(RetailFMV).where(RetailFMV.tenant_id == ctx.tenant_id)
    if model_group:
        st = st.where(RetailFMV.model_group == model_group)
    stale = int(ctx.settings["fmv_stale_days"])
    out = []
    for r in ctx.db.scalars(st.order_by(RetailFMV.as_of_date.desc())):
        d = _j(fmv_row_dict(r))
        d["age_days"] = (date.today() - r.as_of_date).days
        d["stale"] = d["age_days"] > stale
        out.append(d)
    return out


def _add_fmv(ctx: Ctx, body: FmvIn) -> RetailFMV:
    err = validate_source(body.tier, body.source)
    if err:
        raise HTTPException(400, err)
    data = body.model_dump()
    data["as_of_date"] = data["as_of_date"] or date.today()
    r = RetailFMV(tenant_id=ctx.tenant_id, **data)
    ctx.db.add(r)
    return r


@app.post("/api/fmv")
def add_fmv(body: FmvIn, ctx: Ctx = Depends(current_ctx)):
    r = _add_fmv(ctx, body)
    ctx.db.commit()
    return _j(fmv_row_dict(r))


@app.delete("/api/fmv/{fid}")
def delete_fmv(fid: int, ctx: Ctx = Depends(current_ctx)):
    r = ctx.db.get(RetailFMV, fid)
    if not r or r.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "Not found")
    ctx.db.delete(r)
    ctx.db.commit()
    return {"deleted": fid}


@app.post("/api/fmv/import")
async def import_fmv(file: UploadFile = File(...), ctx: Ctx = Depends(current_ctx)):
    rows = read_csv_rows(await file.read())
    ok, errors = 0, []
    for i, r in enumerate(rows, start=2):
        try:
            body = FmvIn(
                region=r.get("region") or None, model_group=r["model_group"],
                year_lo=int(r.get("year_lo") or r["year"]), year_hi=int(r.get("year_hi") or r["year"]),
                miles_lo=int(float(r["miles_lo"])) if r.get("miles_lo") else None,
                miles_hi=int(float(r["miles_hi"])) if r.get("miles_hi") else None,
                tier=(r.get("tier") or "private").lower(), price=float(r["price"].replace(",", "").replace("$", "")),
                is_asking=(r.get("is_asking", "y").lower() in ("y", "yes", "true", "1")),
                as_of_date=date.fromisoformat(r["as_of_date"]) if r.get("as_of_date") else None,
                source=r.get("source") or None, url=r.get("url") or None, notes=r.get("notes") or None)
            _add_fmv(ctx, body)
            ok += 1
        except (KeyError, ValueError, HTTPException) as e:
            errors.append(f"row {i}: {getattr(e, 'detail', e)}")
    ctx.db.commit()
    return {"imported": ok, "errors": errors}


# ================================================================== outcomes & backtest
class OutcomeIn(BaseModel):
    target_lot_id: int | None = None
    sale_id: str | None = None
    lot_no: str | None = None
    did_bid: bool = False
    won: bool = False
    our_max_bid: float | None = None
    final_hammer: float | None = None
    all_in_paid: float | None = None
    condition_on_arrival: str | None = None
    repair_costs: float | None = None
    resale_price: float | None = None
    lease_outcome: str | None = None
    share_with_pool: bool = False


@app.get("/api/outcomes")
def list_outcomes(ctx: Ctx = Depends(current_ctx)):
    rows = ctx.db.scalars(select(Outcome).where(Outcome.tenant_id == ctx.tenant_id).order_by(Outcome.id.desc()))
    out = [_j({c.name: getattr(o, c.name) for c in Outcome.__table__.columns}) for o in rows]
    # calibration: predicted p50 vs actual hammer
    pairs = [(o["predicted_p50"], o["final_hammer"]) for o in out if o["predicted_p50"] and o["final_hammer"]]
    calib = None
    if pairs:
        ratios = [a / p for p, a in pairs if p]
        calib = {"n": len(pairs), "mean_actual_over_predicted": round(sum(ratios) / len(ratios), 3),
                 "mape": round(sum(abs(a - p) / a * 100 for p, a in pairs) / len(pairs), 1)}
    repairs = [o["repair_costs"] for o in out if o["won"] and o["repair_costs"] is not None]
    return {"outcomes": out, "calibration": calib,
            "avg_repair_on_wins": round(sum(repairs) / len(repairs), 0) if repairs else None}


@app.post("/api/outcomes")
def add_outcome(body: OutcomeIn, ctx: Ctx = Depends(current_ctx)):
    data = body.model_dump()
    o = Outcome(tenant_id=ctx.tenant_id, **data)
    if body.target_lot_id:
        t = _get_target(ctx, body.target_lot_id)
        o.sale_id = o.sale_id or t.sale_id
        o.lot_no = o.lot_no or t.lot_no
        snap = t.last_recommendation or {}
        o.predicted_p50 = snap.get("p50")
        o.predicted_ceiling = (snap.get("hammer") or {}).get("hard_ceiling")
        t.status = "won" if body.won else ("lost" if body.did_bid else "skipped")
    ctx.db.add(o)
    ctx.db.commit()
    return _j({c.name: getattr(o, c.name) for c in Outcome.__table__.columns})


@app.get("/api/backtest")
def backtest_ep(sale_id: str | None = None, limit: int = 200, ctx: Ctx = Depends(current_ctx)):
    comps = load_comps(ctx.db, ctx.tenant_id, ctx.settings)
    return backtest(comps, ctx.settings, sale_id, min(limit, 1000))


# ================================================================== static UI
WEB_DIR = BASE_DIR / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(WEB_DIR) / "index.html")
