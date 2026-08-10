"""Add the active runtime guard and idempotency request fingerprint."""

from alembic import op
import sqlalchemy as sa


revision = "0003_runtime_lock_hash"
down_revision = "0002_ai_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_idempotency_records",
        sa.Column("request_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index(
        "uq_meeting_runtime_active_meeting",
        "meeting_runtime_sessions",
        ["meeting_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('COMPLETED', 'FAILED')"),
        sqlite_where=sa.text("status NOT IN ('COMPLETED', 'FAILED')"),
    )


def downgrade() -> None:
    op.drop_index("uq_meeting_runtime_active_meeting", table_name="meeting_runtime_sessions")
    op.drop_column("meeting_idempotency_records", "request_hash")
