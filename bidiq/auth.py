"""Minimal multi-tenant auth: PBKDF2 password hashes + opaque bearer tokens."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import SUPERADMIN_EMAILS, TOKEN_TTL_DAYS, merged_settings
from .db import get_db
from .models import AuthToken, Tenant, User

_ITER = 200_000


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), _ITER)
    return f"pbkdf2${_ITER}${salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, hexdk = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(it))
    return hmac.compare_digest(dk.hex(), hexdk)


def issue_token(db: Session, user: User) -> str:
    tok = secrets.token_urlsafe(32)
    db.add(AuthToken(token=tok, user_id=user.id, expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=TOKEN_TTL_DAYS)))
    db.commit()
    return tok


class Ctx:
    """Request context: the authenticated user, tenant and merged settings."""

    def __init__(self, db: Session, user: User, tenant: Tenant):
        self.db = db
        self.user = user
        self.tenant = tenant
        self.tenant_id = tenant.id
        self.settings = merged_settings(tenant.settings)
        self.is_superadmin = user.email.lower() in SUPERADMIN_EMAILS
        self.is_admin = user.role == "admin" or self.is_superadmin

    def require_admin(self) -> None:
        if not self.is_admin:
            raise HTTPException(403, "Only workspace admins can do this.")


def revoke_tokens(db: Session, user_id: int) -> None:
    for t in db.query(AuthToken).filter(AuthToken.user_id == user_id).all():
        db.delete(t)


def current_ctx(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Ctx:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    tok = db.get(AuthToken, authorization.split(" ", 1)[1].strip())
    if not tok or tok.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, tok.user_id)
    tenant = db.get(Tenant, user.tenant_id) if user else None
    if not user or not tenant:
        raise HTTPException(401, "Unknown user")
    return Ctx(db, user, tenant)
