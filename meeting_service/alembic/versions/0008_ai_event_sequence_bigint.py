"""Allow epoch-based AI callback sequences."""

from alembic import op
import sqlalchemy as sa


revision = "0008_ai_event_sequence_bigint"
down_revision = "0007_minutes_analysis_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "meeting_ai_events",
        "sequence",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "meeting_ai_events",
        "sequence",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
