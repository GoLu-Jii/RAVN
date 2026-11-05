from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("target_id", "source_type", "source_key", name="uq_event_target_source_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_key: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048))
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    one_liner: Mapped[str] = mapped_column(String(500), nullable=False)
    collection_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_baseline: Mapped[bool] = mapped_column(default=False, nullable=False)


class DailySourceCheckpoint(Base):
    __tablename__ = "daily_source_checkpoints"
    __table_args__ = (
        UniqueConstraint("target_id", "source_type", name="uq_daily_checkpoint_target_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    last_success_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_content_hash: Mapped[str | None] = mapped_column(String(64))
    undated_items_baselined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailySourceSummary(Base):
    __tablename__ = "daily_source_summaries"
    __table_args__ = (
        UniqueConstraint("target_id", "collection_date", "source_type", name="uq_daily_summary_target_date_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), nullable=False, index=True)
    collection_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    highlight: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
