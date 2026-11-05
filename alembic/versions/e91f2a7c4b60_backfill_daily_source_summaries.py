"""backfill dated source highlights for events already stored

Revision ID: e91f2a7c4b60
Revises: d8e4c1f7a260
Create Date: 2026-11-02
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e91f2a7c4b60"
down_revision: Union[str, Sequence[str], None] = "d8e4c1f7a260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    events = sa.table(
        "events",
        sa.column("id", sa.Integer()),
        sa.column("target_id", sa.Integer()),
        sa.column("collection_date", sa.Date()),
        sa.column("source_type", sa.String()),
        sa.column("one_liner", sa.String()),
        sa.column("ingested_at", sa.DateTime(timezone=True)),
        sa.column("is_baseline", sa.Boolean()),
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
    rows = connection.execute(
        sa.select(
            events.c.id,
            events.c.target_id,
            events.c.collection_date,
            events.c.source_type,
            events.c.one_liner,
            events.c.ingested_at,
        )
        .where(events.c.is_baseline.is_(False))
        .order_by(
            events.c.target_id,
            events.c.collection_date,
            events.c.source_type,
            events.c.ingested_at.desc(),
            events.c.id.desc(),
        )
    ).fetchall()

    grouped = {}
    for row in rows:
        key = (row.target_id, row.collection_date, row.source_type)
        item = grouped.setdefault(key, {"count": 0, "highlights": [], "collected_at": None})
        item["count"] += 1
        if len(item["highlights"]) < 3:
            item["highlights"].append(row.one_liner)
        if item["collected_at"] is None:
            item["collected_at"] = row.ingested_at

    for (target_id, collection_date, source_type), item in grouped.items():
        highlight = "; ".join(item["highlights"])
        remaining = item["count"] - len(item["highlights"])
        if remaining:
            highlight += f"; and {remaining} more event(s)"
        connection.execute(summaries.insert().values(
            target_id=target_id,
            collection_date=collection_date,
            source_type=source_type,
            status="success",
            event_count=item["count"],
            highlight=highlight,
            collected_at=item["collected_at"] or datetime.now(timezone.utc),
        ))


def downgrade() -> None:
    # The summaries table is owned by the prior migration. This migration only
    # backfills derived rows, so its downgrade intentionally leaves them intact.
    pass
