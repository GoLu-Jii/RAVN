"""store claimed source lists and mismatch audit on weekly signals

Revision ID: f9c2a1d7e604
Revises: e5a1c7d9b304
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9c2a1d7e604"
down_revision: Union[str, Sequence[str], None] = "e5a1c7d9b304"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "weekly_signals",
        sa.Column("claimed_sources", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "weekly_signals",
        sa.Column("source_claim_mismatch", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("weekly_signals", "source_claim_mismatch")
    op.drop_column("weekly_signals", "claimed_sources")
