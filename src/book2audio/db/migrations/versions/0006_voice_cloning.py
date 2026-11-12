"""Voice cloning: cloned_voices, worker_heartbeats, jobs.requires_clone

``cloned_voices`` holds the voices users enrolled from their own recording (the clip itself
lives in object storage under ``voice-refs/<user id>/<voice id>.wav``). ``worker_heartbeats``
lets a local worker advertise what it can do (``clone:qwen3``), so the API knows whether a
voice can be used right now. ``jobs.requires_clone`` marks jobs that name a cloned voice:
only a capable worker may claim them.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-12

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cloned_voices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("gender", sa.String(1), nullable=False),
        sa.Column("ref_object_key", sa.Text(), nullable=False),
        sa.Column("ref_text", sa.Text(), nullable=False),
        sa.Column("ref_sha256", sa.String(64), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("gender IN ('m', 'f')", name="ck_cloned_voices_gender"),
    )
    op.create_index("ix_cloned_voices_user_id", "cloned_voices", ["user_id"])

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(80), primary_key=True),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.add_column(
        "jobs",
        sa.Column("requires_clone", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    # Older code would claim these jobs and fail on the voice id it does not know, so they
    # are failed first with a clear reason (finished_at too: the cleanup sweep needs it).
    op.execute(
        "UPDATE jobs SET status = 'failed', finished_at = CURRENT_TIMESTAMP, "
        "error_message = 'Voice cloning was rolled back before this job ran' "
        "WHERE requires_clone AND status IN ('queued', 'running', 'awaiting_cast_review')"
    )
    op.drop_column("jobs", "requires_clone")
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_cloned_voices_user_id", table_name="cloned_voices")
    op.drop_table("cloned_voices")
