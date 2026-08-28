"""SQLAlchemy engine/session wiring for the Neon Postgres database.

A single module-level engine is created from ``DATABASE_URL`` (sync psycopg
driver) so both the API process and the worker's short-lived sessions share
the same pooling behavior.
"""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return _use_psycopg_driver(url)


def _use_psycopg_driver(url: str) -> str:
    # A bare postgres(ql):// URL (what Neon and most providers hand out) makes
    # SQLAlchemy default to psycopg2, which this project doesn't depend on —
    # it depends on psycopg (v3). Force the right driver explicitly.
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


def _make_engine(url: str):
    # FastAPI dispatches sync route handlers to worker threads. SQLite's default
    # SingletonThreadPool caches one connection per thread, so a request thread
    # can get a different (schema-less, for :memory:) connection than whatever
    # thread created the tables. StaticPool + check_same_thread=False shares one
    # connection across all threads, avoiding that trap. Postgres (production)
    # is unaffected — psycopg is a real connection-pooled driver.
    if url.startswith("sqlite"):
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(url, pool_pre_ping=True)


engine = _make_engine(_database_url())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session() -> Generator[Session, None, None]:
    """FastAPI-style dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
