"""add daily events and source checkpoints

Revision ID: c4f3b2a1d9e0
Revises: a44f447a2934
Create Date: 2026-10-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4f3b2a1d9e0"
down_revision: Union[str, Sequence[str], None] = "a44f447a2934"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("one_liner", sa.String(length=500), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("is_baseline", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.UniqueConstraint("target_id", "source_type", "source_key", name="uq_event_target_source_key"),
    )
    op.create_index("ix_events_target_id", "events", ["target_id"])
    op.create_index("ix_events_source_type", "events", ["source_type"])

    op.create_table(
        "daily_source_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("target_id", "source_type", name="uq_daily_checkpoint_target_source"),
    )
    op.create_index("ix_daily_source_checkpoints_target_id", "daily_source_checkpoints", ["target_id"])


def downgrade() -> None:
    op.drop_index("ix_daily_source_checkpoints_target_id", table_name="daily_source_checkpoints")
    op.drop_table("daily_source_checkpoints")
    op.drop_index("ix_events_source_type", table_name="events")
    op.drop_index("ix_events_target_id", table_name="events")
    op.drop_table("events")
