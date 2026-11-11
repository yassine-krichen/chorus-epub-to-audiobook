"""Migration 0005 (jobs.cast_review_requested_at) goes up, and comes back down safely.

Run against SQLite with just the columns the migration touches: the earlier migrations
use Postgres types, but 0005 only adds and drops one column and updates a status.
"""

from __future__ import annotations

import importlib.util
import pathlib

import sqlalchemy as sa
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_VERSIONS = _ROOT / "src" / "book2audio" / "db" / "migrations" / "versions"


def _load_migration():
    path = next(_VERSIONS.glob("0005_*.py"))
    spec = importlib.util.spec_from_file_location("migration_0005", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _jobs_engine():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE jobs (id INTEGER PRIMARY KEY, status VARCHAR NOT NULL, "
                "error_message TEXT, finished_at TIMESTAMP)"
            )
        )
        conn.execute(sa.text("INSERT INTO jobs (id, status) VALUES (1, 'awaiting_cast_review')"))
        conn.execute(sa.text("INSERT INTO jobs (id, status) VALUES (2, 'running')"))
    return engine


def _columns(conn) -> set[str]:
    return {column["name"] for column in sa.inspect(conn).get_columns("jobs")}


def _run(conn, step) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        step()


def test_the_migration_chain_stays_linear_and_0005_follows_0004():
    config = AlembicConfig(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "src" / "book2audio" / "db" / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    assert len(scripts.get_heads()) == 1
    assert scripts.get_revision("0005").down_revision == "0004"


def test_upgrade_adds_the_column_and_downgrade_removes_it():
    migration = _load_migration()
    with _jobs_engine().begin() as conn:
        _run(conn, migration.upgrade)
        assert "cast_review_requested_at" in _columns(conn)

        _run(conn, migration.downgrade)
        assert "cast_review_requested_at" not in _columns(conn)


def test_downgrade_fails_jobs_that_were_waiting_for_review_and_leaves_others_alone():
    migration = _load_migration()
    with _jobs_engine().begin() as conn:
        _run(conn, migration.upgrade)

        _run(conn, migration.downgrade)

        rows = dict(conn.execute(sa.text("SELECT id, status FROM jobs")).all())
        assert rows == {1: "failed", 2: "running"}
        message = conn.execute(sa.text("SELECT error_message FROM jobs WHERE id = 1")).scalar_one()
        assert "rolled back" in message
        finished = dict(conn.execute(sa.text("SELECT id, finished_at FROM jobs")).all())
        assert finished[1] is not None  # so the cleanup sweep can pick the job up
        assert finished[2] is None


def test_upgrading_again_after_a_downgrade_works():
    migration = _load_migration()
    with _jobs_engine().begin() as conn:
        _run(conn, migration.upgrade)
        _run(conn, migration.downgrade)

        _run(conn, migration.upgrade)

        assert "cast_review_requested_at" in _columns(conn)
