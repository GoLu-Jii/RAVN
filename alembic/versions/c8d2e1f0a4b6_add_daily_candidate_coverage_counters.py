"""persist ATS and blog candidate coverage counters

Revision ID: c8d2e1f0a4b6
Revises: b4a8c7d1e203
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d2e1f0a4b6"
down_revision: Union[str, Sequence[str], None] = "b4a8c7d1e203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in (
        "candidates_total",
        "known_items",
        "failed_items",
        "cap_skipped_items",
        "not_checked_items",
    ):
        op.add_column(
            "daily_source_summaries",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for column in (
        "not_checked_items",
        "cap_skipped_items",
        "failed_items",
        "known_items",
        "candidates_total",
    ):
        op.drop_column("daily_source_summaries", column)
