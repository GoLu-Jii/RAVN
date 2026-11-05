"""record historical source dates that only have baseline events

Revision ID: f4c91a6d2e80
Revises: e91f2a7c4b60
Create Date: 2026-11-05
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4c91a6d2e80"
down_revision: Union[str, Sequence[str], None] = "e91f2a7c4b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    events = sa.table(
        "events",
        sa.column("target_id", sa.Integer()),
        sa.column("collection_date", sa.Date()),
        sa.column("source_type", sa.String()),
        sa.column("is_baseline", sa.Boolean()),
        sa.column("ingested_at", sa.DateTime(timezone=True)),
    )
    summaries = sa.table(
        "daily_source_summaries",
        sa.column("target_id", sa.Integer()),
        sa.column("collection_date", sa.Date()),
        sa.column("source_type", sa.String()),
        sa.column("status", sa.String()),
        sa.column("event_count", sa.Integer()),
        sa.column("highlight", sa.Text()),
        sa.column("collected_at", sa.DateTime(timezone=True)),
    )
    baseline_groups = connection.execute(
        sa.select(
            events.c.target_id,
            events.c.collection_date,
            events.c.source_type,
            sa.func.max(events.c.ingested_at).label("collected_at"),
        )
        .where(events.c.is_baseline.is_(True))
        .group_by(events.c.target_id, events.c.collection_date, events.c.source_type)
    ).fetchall()
    existing = {
        (row.target_id, row.collection_date, row.source_type)
        for row in connection.execute(
            sa.select(summaries.c.target_id, summaries.c.collection_date, summaries.c.source_type)
        ).fetchall()
    }

    for row in baseline_groups:
        key = (row.target_id, row.collection_date, row.source_type)
        if key in existing:
            continue
        connection.execute(summaries.insert().values(
            target_id=row.target_id,
            collection_date=row.collection_date,
            source_type=row.source_type,
            status="baseline_only",
            event_count=0,
            highlight="Baseline records were captured; no current-day events were recorded.",
            collected_at=row.collected_at or datetime.now(timezone.utc),
        ))


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM daily_source_summaries WHERE status = 'baseline_only'")
    )
