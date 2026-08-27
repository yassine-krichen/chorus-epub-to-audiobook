"""Shared fixtures for the API/worker test suite.

Sets required env vars *before* anything under test imports book2audio.db.session
(its module-level ``engine`` is built from ``DATABASE_URL`` at import time) or
book2audio.api.auth.security (reads ``JWT_SECRET`` lazily, but we set it up front
for consistency). Tests run fully offline against an in-memory SQLite database —
no real Postgres/R2/GCP credentials required.

SQLite has no native JSONB/UUID types, so we teach its DDL compiler to render
Postgres's JSONB as JSON and UUID as CHAR(32); this is purely a test-time shim,
production still talks to real Postgres via psycopg.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-of-at-least-32-bytes-long")
os.environ.setdefault("CORS_ORIGINS", "")
os.environ.setdefault("CLOUD_RUN_JOB_NAME", "projects/p/locations/l/jobs/test-job")
os.environ.setdefault("R2_BUCKET", "test-bucket")
os.environ.setdefault("R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key-id")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-access-key")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@compiles(UUID, "sqlite")
def _uuid_as_char_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "CHAR(32)"


import book2audio.db.session as db_session_module  # noqa: E402
import book2audio.worker.entrypoint as worker_entrypoint_module  # noqa: E402
import book2audio.worker.progress as worker_progress_module  # noqa: E402
from book2audio.db.models import Base  # noqa: E402

# FastAPI dispatches sync route handlers (plain ``def``) to a worker thread via
# anyio, and SQLAlchemy's default SQLite pool caches one connection *per
# thread* — so the API's own thread would see a table-less, blank in-memory
# database. StaticPool + check_same_thread=False shares one real connection
# across every thread instead, which is what an in-memory test DB needs.
#
# db.session's own ``get_session()`` reads ``SessionLocal`` as a module global
# at call time, so overwriting the attribute is enough there. worker/progress.py
# and worker/entrypoint.py did ``from book2audio.db.session import SessionLocal``,
# which bound their *own* module-level name at import time — that name has to
# be repointed too, or those modules would keep using the original (broken)
# per-thread engine.
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

db_session_module.engine = engine
db_session_module.SessionLocal = SessionLocal
worker_progress_module.SessionLocal = SessionLocal
worker_entrypoint_module.SessionLocal = SessionLocal

Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def _clean_database():
    """Every test starts with empty tables — the in-memory SQLite DB is shared
    process-wide (StaticPool, one connection for every thread), so state would
    otherwise leak between tests."""
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def db_session():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    from book2audio.api.main import app

    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
