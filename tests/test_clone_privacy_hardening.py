"""Retention, cleanup and hardening around cloned voices: the review's suggestions."""

from __future__ import annotations

import importlib.util
import io
import os
import pathlib
import time
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from rich.console import Console
from sqlalchemy.dialects import postgresql

import book2audio.worker.render as render_module
from book2audio.api.voices import cloning
from book2audio.db.models import ClonedVoice, Job, User
from book2audio.tts.clone_engine import remove_stale_workdirs
from book2audio.tts.voices import clone_voice_id
from book2audio.worker import local_daemon

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "src/book2audio/db/migrations/versions"


# --- S2: nothing lingers on the worker --------------------------------------------------------


def _old(path: pathlib.Path, age_s: float) -> None:
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))


def test_stale_clone_temp_folders_are_removed_and_recent_or_foreign_ones_are_kept(tmp_path):
    stale = tmp_path / "b2a-clone-old"
    stale.mkdir()
    (stale / "refs").mkdir()
    (stale / "refs" / "clip.wav").write_bytes(b"personal audio")
    _old(stale, 7200)
    recent = tmp_path / "b2a-clone-running"
    recent.mkdir()
    foreign = tmp_path / "other-old"
    foreign.mkdir()
    _old(foreign, 7200)
    a_file = tmp_path / "b2a-clone-file"
    a_file.write_bytes(b"x")
    _old(a_file, 7200)

    removed = remove_stale_workdirs(root=str(tmp_path))

    assert removed == 1
    assert not stale.exists()
    assert recent.exists() and foreign.exists() and a_file.exists()


def _dead_pid() -> int:
    import subprocess
    import sys

    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_an_old_folder_of_a_process_that_is_still_running_is_never_removed(tmp_path):
    # Another daemon on the same machine, in the middle of a long job.
    running = tmp_path / f"b2a-clone-{os.getpid()}-abc123"
    running.mkdir()
    _old(running, 7200)
    gone = tmp_path / f"b2a-clone-{_dead_pid()}-def456"
    gone.mkdir()
    _old(gone, 7200)

    removed = remove_stale_workdirs(root=str(tmp_path))

    assert removed == 1
    assert running.exists() and not gone.exists()


def test_a_new_engine_names_its_folder_after_its_process(tmp_path):
    from book2audio.tts.clone_engine import QwenCloneEngine

    engine = QwenCloneEngine("python", str(tmp_path))
    try:
        assert engine._workdir.name.startswith(f"b2a-clone-{os.getpid()}-")
    finally:
        engine.close()


def test_the_daemon_clears_stale_temp_folders_when_it_starts(monkeypatch):
    calls = []
    monkeypatch.setattr(local_daemon, "remove_stale_workdirs", lambda: calls.append("swept"))
    monkeypatch.setattr(local_daemon, "_detect_capabilities", lambda: [])

    class Stop(Exception):
        pass

    monkeypatch.setattr(local_daemon.threading, "Thread", lambda **kw: type("T", (), {"start": lambda s: None})())

    def stop_after_start(db, **kw):
        raise Stop

    monkeypatch.setattr(local_daemon, "_claim_next_job", stop_after_start)

    with pytest.raises(Stop):
        local_daemon.main()

    assert calls == ["swept"]


def test_every_heartbeat_also_prunes_the_prompts_of_deleted_voices(monkeypatch):
    beats = {"count": 0, "pruned": 0}

    class FakeEvent:
        def is_set(self):
            return beats["count"] >= 3

        def wait(self, seconds):
            beats["count"] += 1

    monkeypatch.setattr(local_daemon.threading, "Event", FakeEvent)
    monkeypatch.setattr(local_daemon, "record_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(local_daemon, "_prune_cached_prompts", lambda: beats.update(pruned=beats["pruned"] + 1))

    local_daemon._heartbeat_loop("w", [])

    assert beats["pruned"] == 3


def test_a_failed_prune_does_not_stop_the_heartbeat(monkeypatch):
    seen = {"beats": 0, "count": 0}

    class FakeEvent:
        def is_set(self):
            return seen["count"] >= 2

        def wait(self, seconds):
            seen["count"] += 1

    def boom():
        raise OSError("disk")

    monkeypatch.setattr(local_daemon.threading, "Event", FakeEvent)
    monkeypatch.setattr(local_daemon, "record_heartbeat", lambda *a, **k: seen.update(beats=seen["beats"] + 1))
    monkeypatch.setattr(local_daemon, "_prune_cached_prompts", boom)

    local_daemon._heartbeat_loop("w", [])

    assert seen["beats"] == 2


def test_the_consent_and_its_version_state_how_long_a_deleted_voice_lingers():
    assert cloning.CONSENT_VERSION != "2026-11-v1"  # the wording changed, so people agree again
    assert "removed from storage at once" in cloning.CONSENT_TEXT
    assert "within about a minute" in cloning.CONSENT_TEXT


# --- S3: the downgrade will not orphan recordings ----------------------------------------------


def _migration():
    spec = importlib.util.spec_from_file_location("m0006", next(_VERSIONS.glob("0006_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgraded_engine():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
        conn.execute(
            sa.text(
                "CREATE TABLE jobs (id INTEGER PRIMARY KEY, status VARCHAR NOT NULL, "
                "error_message TEXT, finished_at TIMESTAMP)"
            )
        )
        with Operations.context(MigrationContext.configure(conn)):
            _migration().upgrade()
    return engine


def test_the_downgrade_refuses_while_cloned_voices_exist_and_changes_nothing():
    engine = _upgraded_engine()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO cloned_voices (id, user_id, name, gender, ref_object_key, ref_text, "
                "ref_sha256, duration_s, consent_at, consent_version) VALUES "
                "('a', 'u', 'Me', 'f', 'k', 't', 'h', 12.0, CURRENT_TIMESTAMP, 'v')"
            )
        )
        conn.execute(sa.text("INSERT INTO jobs (id, status, requires_clone) VALUES (1, 'queued', 1)"))

    with pytest.raises(RuntimeError, match="Delete them first"):
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                _migration().downgrade()

    with engine.connect() as conn:
        assert "cloned_voices" in sa.inspect(conn).get_table_names()
        assert conn.execute(sa.text("SELECT status FROM jobs WHERE id = 1")).scalar_one() == "queued"


def test_the_downgrade_still_works_once_the_voices_are_gone():
    engine = _upgraded_engine()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            _migration().downgrade()
        assert "cloned_voices" not in sa.inspect(conn).get_table_names()


# --- S4: the five-voice limit is checked under a lock ------------------------------------------


def test_the_voice_count_is_taken_while_holding_the_users_row():
    sql = str(cloning._lock_user(uuid.uuid4()).compile(dialect=postgresql.dialect()))

    assert "FROM users" in sql and "FOR UPDATE" in sql


def test_the_lock_is_taken_before_the_voices_are_counted(db_session, monkeypatch):
    user = User(email="lock@example.com", password_hash="h")
    db_session.add(user)
    db_session.commit()
    order: list[str] = []
    real_execute = db_session.execute
    real_scalar = db_session.scalar

    def execute(statement, *a, **k):
        if "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect())):
            order.append("lock")
        return real_execute(statement, *a, **k)

    def scalar(statement, *a, **k):
        if "count" in str(statement).lower():
            order.append("count")
        return real_scalar(statement, *a, **k)

    monkeypatch.setattr(db_session, "execute", execute)
    monkeypatch.setattr(db_session, "scalar", scalar)
    monkeypatch.setattr(cloning, "read_object", lambda key, max_bytes: None)

    with pytest.raises(Exception):  # the upload is missing: we only care about the order
        cloning.create_cloned_voice(
            db_session,
            user.id,
            voice_id=uuid.uuid4(),
            name="Me",
            gender="m",
            script_id="lighthouse",
            consent=True,
            consent_version=cloning.CONSENT_VERSION,
        )

    assert order[:2] == ["lock", "count"]


# --- S8: a voice name is data, not console markup ----------------------------------------------


def test_a_voice_name_with_markup_is_printed_literally(db_session, tmp_path, monkeypatch):
    user = User(email="markup@example.com", password_hash="h")
    db_session.add(user)
    db_session.commit()
    voice_id = uuid.uuid4()
    db_session.add(
        ClonedVoice(
            id=voice_id,
            user_id=user.id,
            name="[bold red]Evil[/bold red] [link=http://x]click[/link]",
            gender="m",
            ref_object_key=f"voice-refs/{user.id}/{voice_id}.wav",
            ref_text="t",
            ref_sha256="0" * 64,
            duration_s=15.0,
            consent_at=datetime.now(timezone.utc),
            consent_version="v",
        )
    )
    job = Job(
        user_id=user.id, title="B", epub_object_key="k", config={}, status="queued", chapters=[], parts=[]
    )
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(render_module, "download_capped", lambda *a, **k: False)  # clip missing
    from book2audio.config import Config

    config = Config(
        epub_path=str(tmp_path / "b.epub"),
        output_path=str(tmp_path / "b.m4b"),
        work_dir=str(tmp_path),
        narrator_voice=clone_voice_id(voice_id),
    )
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=200)

    render_module._load_clone_references(job.id, str(tmp_path), config, console)

    printed = output.getvalue()
    assert "[bold red]Evil[/bold red]" in printed
    assert "[link=http://x]click[/link]" in printed
