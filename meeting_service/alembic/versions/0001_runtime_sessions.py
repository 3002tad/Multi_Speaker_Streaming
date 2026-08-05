"""Create Meeting Service runtime and idempotency tables.

Revision ID: 0001_runtime_sessions
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_runtime_sessions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_runtime_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("meeting_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("livekit_room", sa.String(200), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "meeting_idempotency_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("operation", "key", name="uq_meeting_idempotency_operation_key"),
    )


def downgrade() -> None:
    op.drop_table("meeting_idempotency_records")
    op.drop_table("meeting_runtime_sessions")
