"""Merge the runtime idempotency and transcript/minutes migration branches.

Revision ID: 0005_merge_runtime_minutes
Revises: 0004_minutes_exports, 0003_runtime_lock_hash
"""


revision = "0005_merge_runtime_minutes"
down_revision = ("0004_minutes_exports", "0003_runtime_lock_hash")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two schema branches; all schema changes are in parent revisions."""


def downgrade() -> None:
    """Split the two schema branches; all schema changes are in parent revisions."""
