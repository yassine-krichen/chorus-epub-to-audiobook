"""ORM models for the API/worker control-plane database.

Distinct from :mod:`book2audio.models`, which holds the pipeline's in-memory
domain dataclasses (Book, Chapter, Segment). These two module trees serve
different purposes and are kept separate on purpose.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, func
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

    user: Mapped[User] = relationship(back_populates="jobs")
