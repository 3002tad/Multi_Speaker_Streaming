"""Persist transcript segments and minutes revisions."""

from alembic import op
import sqlalchemy as sa


revision = "0003_transcript_minutes"
down_revision = "0002_ai_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_transcript_segments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("segment_id", sa.String(160), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("meeting_id", "segment_id", name="uq_meeting_transcript_segment"),
    )
    op.create_index("ix_meeting_transcript_segments_meeting_id", "meeting_transcript_segments", ["meeting_id"])
    op.create_table(
        "meeting_minutes_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False),
        sa.Column("source_segment_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("meeting_id", "revision", name="uq_meeting_minutes_revision"),
    )
    op.create_index("ix_meeting_minutes_revisions_meeting_id", "meeting_minutes_revisions", ["meeting_id"])
    op.create_index("ix_meeting_minutes_revisions_status", "meeting_minutes_revisions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_meeting_minutes_revisions_status", table_name="meeting_minutes_revisions")
    op.drop_index("ix_meeting_minutes_revisions_meeting_id", table_name="meeting_minutes_revisions")
    op.drop_table("meeting_minutes_revisions")
    op.drop_index("ix_meeting_transcript_segments_meeting_id", table_name="meeting_transcript_segments")
    op.drop_table("meeting_transcript_segments")
