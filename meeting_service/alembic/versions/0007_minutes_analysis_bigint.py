"""Allow full-width transcript snapshot fingerprints."""

from alembic import op
import sqlalchemy as sa


revision = "0007_minutes_analysis_bigint"
down_revision = "0006_minutes_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "meeting_minutes_analyses",
        "base_transcript_revision",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "meeting_minutes_analyses",
        "base_transcript_revision",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
