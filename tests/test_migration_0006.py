"""Migration 0006 (voice cloning tables and jobs.requires_clone) goes up and comes back down.

Run against SQLite with minimal ``users`` and ``jobs`` tables: the migration only adds to them.
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
    path = next(_VERSIONS.glob("0006_*.py"))
    spec = importlib.util.spec_from_file_location("migration_0006", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
        conn.execute(
            sa.text(
                "CREATE TABLE jobs (id INTEGER PRIMARY KEY, status VARCHAR NOT NULL, "
                "error_message TEXT, finished_at TIMESTAMP)"
            )
        )
        conn.execute(sa.text("INSERT INTO jobs (id, status) VALUES (1, 'queued')"))
    return engine


def _run(conn, step) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        step()


def _tables(conn) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def test_the_migration_chain_stays_linear_and_0006_follows_0005():
    config = AlembicConfig(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src" / "book2audio" / "db" / "migrations")
    )
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0006"]
    assert scripts.get_revision("0006").down_revision == "0005"


def test_upgrade_creates_the_tables_and_the_job_flag_and_downgrade_removes_them():
    migration = _load_migration()
    with _engine().begin() as conn:
        _run(conn, migration.upgrade)
        assert {"cloned_voices", "worker_heartbeats"} <= _tables(conn)
        job_columns = {c["name"] for c in sa.inspect(conn).get_columns("jobs")}
        assert "requires_clone" in job_columns
        voice_columns = {c["name"] for c in sa.inspect(conn).get_columns("cloned_voices")}
        assert {
            "id",
            "user_id",
            "name",
            "gender",
            "ref_object_key",
            "ref_text",
            "duration_s",
            "consent_at",
            "consent_version",
            "created_at",
        } <= voice_columns

        _run(conn, migration.downgrade)

        assert not ({"cloned_voices", "worker_heartbeats"} & _tables(conn))
        assert "requires_clone" not in {c["name"] for c in sa.inspect(conn).get_columns("jobs")}


def test_existing_jobs_do_not_require_a_clone_engine():
    migration = _load_migration()
    with _engine().begin() as conn:
        _run(conn, migration.upgrade)

        flag = conn.execute(sa.text("SELECT requires_clone FROM jobs WHERE id = 1")).scalar_one()

        assert not flag


def test_the_gender_column_only_takes_m_or_f():
    migration = _load_migration()
    with _engine().begin() as conn:
        _run(conn, migration.upgrade)
        row = (
            "INSERT INTO cloned_voices (id, user_id, name, gender, ref_object_key, ref_text, "
            "ref_sha256, duration_s, consent_at, consent_version) "
            "VALUES ('{id}', 'u', 'Me', '{gender}', 'k', 't', 'h', 12.0, CURRENT_TIMESTAMP, 'v1')"
        )
        conn.execute(sa.text(row.format(id="a", gender="f")))

        try:
            conn.execute(sa.text(row.format(id="b", gender="x")))
        except sa.exc.IntegrityError:
            return
        raise AssertionError("a gender other than m or f was accepted")


def test_downgrade_fails_unfinished_clone_jobs_and_leaves_others_alone():
    migration = _load_migration()
    with _engine().begin() as conn:
        _run(conn, migration.upgrade)
        conn.execute(sa.text("UPDATE jobs SET requires_clone = 1 WHERE id = 1"))
        conn.execute(sa.text("INSERT INTO jobs (id, status) VALUES (2, 'queued')"))
        conn.execute(sa.text("INSERT INTO jobs (id, status) VALUES (3, 'succeeded')"))
        conn.execute(sa.text("UPDATE jobs SET requires_clone = 1 WHERE id = 3"))

        _run(conn, migration.downgrade)

        rows = dict(conn.execute(sa.text("SELECT id, status FROM jobs")).all())
        assert rows == {1: "failed", 2: "queued", 3: "succeeded"}
        message = conn.execute(sa.text("SELECT error_message FROM jobs WHERE id = 1")).scalar_one()
        assert "rolled back" in message
        finished = conn.execute(sa.text("SELECT finished_at FROM jobs WHERE id = 1")).scalar_one()
        assert finished is not None


def test_upgrading_again_after_a_downgrade_works():
    migration = _load_migration()
    with _engine().begin() as conn:
        _run(conn, migration.upgrade)
        _run(conn, migration.downgrade)

        _run(conn, migration.upgrade)

        assert "cloned_voices" in _tables(conn)
