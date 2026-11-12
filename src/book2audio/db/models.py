"""ORM models for the API/worker control-plane database.

Distinct from :mod:`book2audio.models`, which holds the pipeline's in-memory
domain dataclasses (Book, Chapter, Segment). These two module trees serve
different purposes and are kept separate on purpose.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, String, Text, false, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user")
    jobs: Mapped[list["Job"]] = relationship(back_populates="user")
    cloned_voices: Mapped[list["ClonedVoice"]] = relationship(back_populates="user")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True, unique=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    epub_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    total_chapters: Mapped[int | None] = mapped_column(nullable=True)
    chapters: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    parts: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cloud_run_execution_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    artifacts_swept_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # When the worker paused the job for the user to review the cast; the review
    # window (and its expiry) counts from here.
    cast_review_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # The job names a cloned voice, so only a worker with the clone engine may claim it.
    requires_clone: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    user: Mapped[User] = relationship(back_populates="jobs")


class ClonedVoice(Base):
    """A voice the user enrolled from a recording of their own voice."""

    __tablename__ = "cloned_voices"
    __table_args__ = (CheckConstraint("gender IN ('m', 'f')", name="ck_cloned_voices_gender"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # "m" or "f", set by the user at enrolment; picks the Kokoro voice used if cloning is unavailable.
    gender: Mapped[str] = mapped_column(String(1), nullable=False)
    ref_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    # The passage the user read: the engine needs it to line the clip up, so no ASR is needed.
    ref_text: Mapped[str] = mapped_column(Text, nullable=False)
    ref_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    consent_at: Mapped[datetime] = mapped_column(nullable=False)
    consent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped[User] = relationship(back_populates="cloned_voices")


class WorkerHeartbeat(Base):
    """A local worker's last sign of life and what it can do (e.g. ``clone:qwen3``)."""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False)
