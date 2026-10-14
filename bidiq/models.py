"""Data model (spec §4, §9, §12, §16).

Designed *wide* from day one: class-aware (box trucks, tractors, reefer trailers)
and multi-tenant (tenant_id on every private row; NULL tenant_id = shared pool).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Sale(Base):
    """An auction event. Shared across tenants (auction terms are public)."""

    __tablename__ = "sales"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_id: Mapped[str] = mapped_column(String(40), unique=True)
    sale_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    auction_house: Mapped[str] = mapped_column(String(80), default="Taylor & Martin")
    buyers_premium_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    deposit_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    wire_deadline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payment_window_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    absolute: Mapped[bool] = mapped_column(Boolean, default=True)
    terms_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_upcoming: Mapped[bool] = mapped_column(Boolean, default=False)


class _VehicleSpec:
    """Columns shared by SoldComp and TargetLot (same schema per spec §4)."""

    sale_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    sale_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lot_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lot_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    make: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_group: Mapped[str | None] = mapped_column(String(60), index=True, nullable=True)
    super_group: Mapped[str | None] = mapped_column(String(60), nullable=True)
    class_raw: Mapped[str | None] = mapped_column(String(40), nullable=True)
    body_class: Mapped[str | None] = mapped_column(String(30), index=True, nullable=True)
    engine_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    engine_family: Mapped[str | None] = mapped_column(String(40), nullable=True)
    desc_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    trans_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    transmission: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sleeper: Mapped[str | None] = mapped_column(String(40), nullable=True)
    miles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    odometer_miles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ecm_miles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tamper_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    hours_metric: Mapped[str | None] = mapped_column(String(20), nullable=True)  # total|engine|electric
    reefer_total_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    reefer_engine_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    reefer_electric_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    door_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # swing|rollup|liftgate
    body_length_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    branded_title: Mapped[bool] = mapped_column(Boolean, default=False)
    non_operable: Mapped[bool] = mapped_column(Boolean, default=False)
    age_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    miles_per_year: Mapped[float | None] = mapped_column(Float, nullable=True)
    lifetime_mph: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(300), nullable=True)


class SoldComp(_VehicleSpec, Base):
    __tablename__ = "sold_comps"
    __table_args__ = (UniqueConstraint("tenant_id", "sale_id", "lot_no", name="uq_comp_sale_lot"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=True)
    sold_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TargetLot(_VehicleSpec, Base):
    __tablename__ = "target_lots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="watch")  # watch|bid|won|lost|skipped
    run_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gvwr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    front_axle_lbs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rear_axle_lbs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    apu: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sleeper_size_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    liftgate: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    engine_brake: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    condition_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    blind_buy: Mapped[bool] = mapped_column(Boolean, default=False)
    buyers_premium_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    transport_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    transport_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    retail_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    checklist: Mapped[dict] = mapped_column(JSON, default=dict)  # vin_sticker_match, nicb, nmvtis, gvwr_confirmed
    last_recommendation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class RetailFMV(Base):
    """Manual retail FMV comps (spec §9 / §16 #3)."""

    __tablename__ = "retail_fmv"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    region: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_group: Mapped[str] = mapped_column(String(60), index=True)
    year_lo: Mapped[int] = mapped_column(Integer)
    year_hi: Mapped[int] = mapped_column(Integer)
    miles_lo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    miles_hi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[str] = mapped_column(String(20), default="private")  # dealer|private|wholesale
    price: Mapped[float] = mapped_column(Float)
    is_asking: Mapped[bool] = mapped_column(Boolean, default=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Outcome(Base):
    """Post-sale log feeding the flywheel (spec §12)."""

    __tablename__ = "outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    target_lot_id: Mapped[int | None] = mapped_column(ForeignKey("target_lots.id"), nullable=True)
    sale_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lot_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    did_bid: Mapped[bool] = mapped_column(Boolean, default=False)
    won: Mapped[bool] = mapped_column(Boolean, default=False)
    our_max_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_hammer: Mapped[float | None] = mapped_column(Float, nullable=True)
    all_in_paid: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_ceiling: Mapped[float | None] = mapped_column(Float, nullable=True)
    condition_on_arrival: Mapped[str | None] = mapped_column(Text, nullable=True)
    repair_costs: Mapped[float | None] = mapped_column(Float, nullable=True)
    resale_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    lease_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    share_with_pool: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class IngestLog(Base):
    __tablename__ = "ingest_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sale_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
