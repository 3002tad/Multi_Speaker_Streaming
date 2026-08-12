"""Persist Meeting Service minutes-analysis control-plane state."""

from alembic import op
import sqlalchemy as sa


revision = "0006_minutes_analysis"
down_revision = "0005_merge_runtime_minutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_minutes_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_session_id", sa.Uuid(), nullable=False),
        sa.Column("base_transcript_revision", sa.Integer(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "meeting_id",
            "base_transcript_revision",
            name="uq_meeting_minutes_analysis_snapshot",
        ),
    )
    op.create_index(
        "ix_meeting_minutes_analyses_meeting_id",
        "meeting_minutes_analyses",
        ["meeting_id"],
    )
    op.create_index(
        "ix_meeting_minutes_analyses_runtime_session_id",
        "meeting_minutes_analyses",
        ["runtime_session_id"],
    )
    op.create_index(
        "ix_meeting_minutes_analyses_status",
        "meeting_minutes_analyses",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_meeting_minutes_analyses_status", table_name="meeting_minutes_analyses")
    op.drop_index("ix_meeting_minutes_analyses_runtime_session_id", table_name="meeting_minutes_analyses")
    op.drop_index("ix_meeting_minutes_analyses_meeting_id", table_name="meeting_minutes_analyses")
    op.drop_table("meeting_minutes_analyses")
