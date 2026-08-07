"""Persist meeting minutes DOCX export metadata."""

from alembic import op
import sqlalchemy as sa


revision = "0004_minutes_exports"
down_revision = "0003_transcript_minutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_minutes_exports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("minutes_revision", sa.Integer(), nullable=False),
        sa.Column("minutes_status", sa.String(20), nullable=False),
        sa.Column("format", sa.String(20), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("meeting_id", "minutes_revision", "format", name="uq_meeting_minutes_export"),
    )
    op.create_index("ix_meeting_minutes_exports_meeting_id", "meeting_minutes_exports", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_meeting_minutes_exports_meeting_id", table_name="meeting_minutes_exports")
    op.drop_table("meeting_minutes_exports")
