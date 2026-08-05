"""Add durable AI callback idempotency records."""

from alembic import op
import sqlalchemy as sa


revision = "0002_ai_events"
down_revision = "0001_runtime_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_ai_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_session_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meeting_ai_events_meeting_id", "meeting_ai_events", ["meeting_id"])
    op.create_index("ix_meeting_ai_events_runtime_session_id", "meeting_ai_events", ["runtime_session_id"])


def downgrade() -> None:
    op.drop_index("ix_meeting_ai_events_runtime_session_id", table_name="meeting_ai_events")
    op.drop_index("ix_meeting_ai_events_meeting_id", table_name="meeting_ai_events")
    op.drop_table("meeting_ai_events")
