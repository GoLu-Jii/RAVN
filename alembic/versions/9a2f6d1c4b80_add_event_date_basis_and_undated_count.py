"""store event date basis and undated source counts

Revision ID: 9a2f6d1c4b80
Revises: c73a9b8e21d4
Create Date: 2026-11-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9a2f6d1c4b80"
down_revision: Union[str, Sequence[str], None] = "c73a9b8e21d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_source_summaries",
        sa.Column("undated_items", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("events", sa.Column("date_basis", sa.String(length=20), nullable=True))

    # Existing baseline rows and snapshots are known to be first-observed.
    op.execute(
        "UPDATE events SET date_basis = 'first_observed' "
        "WHERE is_baseline = 1 OR source_type = 'web_social'"
    )
    # GitHub rows are all derived from source commit timestamps. Older ATS/blog
    # non-baseline rows are left NULL unless their source date can be proven.
    op.execute("UPDATE events SET date_basis = 'source' WHERE source_type = 'github'")

def downgrade() -> None:
    op.drop_column("events", "date_basis")
    op.drop_column("daily_source_summaries", "undated_items")
