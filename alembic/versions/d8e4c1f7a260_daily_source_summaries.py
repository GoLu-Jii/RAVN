"""group daily events and source highlights by collection date

Revision ID: d8e4c1f7a260
Revises: c4f3b2a1d9e0
Create Date: 2026-11-02
"""
from datetime import datetime, timezone
from typing import Sequence, Union
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa


revision: str = "d8e4c1f7a260"
down_revision: Union[str, Sequence[str], None] = "c4f3b2a1d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("collection_date", sa.Date(), nullable=True))
    events = sa.table(
        "events",
        sa.column("id", sa.Integer()),
        sa.column("event_date", sa.DateTime(timezone=True)),
        sa.column("collection_date", sa.Date()),
    )
    connection = op.get_bind()
    rows = connection.execute(sa.select(events.c.id, events.c.event_date)).fetchall()
    india_tz = ZoneInfo("Asia/Kolkata")
    for row in rows:
        event_date = row.event_date
        if isinstance(event_date, str):
            event_date = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
        if event_date.tzinfo is None:
            event_date = event_date.replace(tzinfo=timezone.utc)
        collection_date = event_date.astimezone(india_tz).date()
        connection.execute(
            events.update().where(events.c.id == row.id).values(collection_date=collection_date)
        )
    with op.batch_alter_table("events") as batch_op:
        batch_op.alter_column("collection_date", existing_type=sa.Date(), nullable=False)
    op.create_index("ix_events_collection_date", "events", ["collection_date"])

    op.add_column("daily_source_checkpoints", sa.Column("last_content_hash", sa.String(length=64), nullable=True))

    op.create_table(
        "daily_source_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("collection_date", sa.Date(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("highlight", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "target_id", "collection_date", "source_type",
            name="uq_daily_summary_target_date_source",
        ),
    )
    op.create_index("ix_daily_source_summaries_target_id", "daily_source_summaries", ["target_id"])
    op.create_index("ix_daily_source_summaries_collection_date", "daily_source_summaries", ["collection_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_source_summaries_collection_date", table_name="daily_source_summaries")
    op.drop_index("ix_daily_source_summaries_target_id", table_name="daily_source_summaries")
    op.drop_table("daily_source_summaries")
    op.drop_column("daily_source_checkpoints", "last_content_hash")
    op.drop_index("ix_events_collection_date", table_name="events")
    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_column("collection_date")
