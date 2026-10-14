from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL


class Base(DeclarativeBase):
    pass


def make_engine(url: str = DATABASE_URL):
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(eng=None) -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(eng or engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
