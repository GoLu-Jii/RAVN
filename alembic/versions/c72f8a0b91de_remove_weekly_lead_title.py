"""remove redundant title from weekly leads

Revision ID: c72f8a0b91de
Revises: ab12cd34ef56
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c72f8a0b91de"
down_revision: Union[str, Sequence[str], None] = "ab12cd34ef56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("weekly_leads", "title")


def downgrade() -> None:
    op.add_column(
        "weekly_leads",
        sa.Column("title", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text("UPDATE weekly_leads SET title = description WHERE title IS NULL")
    )
    with op.batch_alter_table("weekly_leads") as batch_op:
        batch_op.alter_column(
            "title",
            existing_type=sa.Text(),
            nullable=False,
        )
