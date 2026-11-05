"""track initial snapshots for undated ATS and blog items

Revision ID: c73a9b8e21d4
Revises: f4c91a6d2e80
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c73a9b8e21d4"
down_revision: Union[str, Sequence[str], None] = "f4c91a6d2e80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_source_checkpoints",
        sa.Column("undated_items_baselined_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("daily_source_checkpoints", "undated_items_baselined_at")
