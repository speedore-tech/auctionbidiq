"""Create (or reset the password of) a tenant admin login. Used on the VPS instead of public sign-up.

    python scripts/create_admin.py                       # reads SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD / SEED_TENANT_NAME
    python scripts/create_admin.py you@x.com "Tenant"    # prompts for the password
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from bidiq.auth import hash_password  # noqa: E402
from bidiq.db import SessionLocal, init_db  # noqa: E402
from bidiq.models import Tenant, User  # noqa: E402


def main() -> int:
    email = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SEED_ADMIN_EMAIL", "")).strip().lower()
    tenant_name = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SEED_TENANT_NAME", "Speedore")
    password = os.environ.get("SEED_ADMIN_PASSWORD") if len(sys.argv) <= 1 else None
    if not email:
        print("Need an email (arg 1 or SEED_ADMIN_EMAIL)", file=sys.stderr)
        return 1
    if not password:
        password = getpass.getpass(f"Password for {email}: ")
    if len(password) < 8:
        print("Password must be at least 8 characters", file=sys.stderr)
        return 1
    init_db()
    db = SessionLocal()
    user = db.scalar(select(User).where(User.email == email))
    if user:
        user.password_hash = hash_password(password)
        print(f"Reset password for {email}")
    else:
        t = Tenant(name=tenant_name, settings={})
        db.add(t)
        db.flush()
        db.add(User(tenant_id=t.id, email=email, password_hash=hash_password(password), role="admin"))
        print(f"Created tenant '{tenant_name}' with admin {email}")
    db.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
