"""Add jobs.cast_review_requested_at

A job can now pause in the status ``awaiting_cast_review`` while the user fixes the
detected cast. ``status`` is a plain string, so the new status needs no schema change;
this column records when the pause began so the review can expire.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("cast_review_requested_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    # Jobs paused for review would lose their place, so they are failed first
    # rather than left in a status the older code does not know.
    op.execute(
        # finished_at too: the cleanup sweep only picks up finished jobs.
        "UPDATE jobs SET status = 'failed', finished_at = CURRENT_TIMESTAMP, "
        "error_message = 'Cast review was rolled back before it was confirmed' "
        "WHERE status = 'awaiting_cast_review'"
    )
    op.drop_column("jobs", "cast_review_requested_at")
