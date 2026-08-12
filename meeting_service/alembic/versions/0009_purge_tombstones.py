"""Persist purge cleanup intent for retryable object-storage deletion."""

from alembic import op
import sqlalchemy as sa


revision = "0009_purge_tombstones"
down_revision = "0008_ai_event_sequence_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_purge_tombstones",
        sa.Column("meeting_id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("pending_storage_keys", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("meeting_purge_tombstones")

