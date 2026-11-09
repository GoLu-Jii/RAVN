"""Persistence models for weekly analysis attempts, results, and signals."""
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WeeklyRun(Base):
    __tablename__ = "weekly_runs"
    __table_args__ = (
        UniqueConstraint(
            "target_id", "week_start_date", "attempt", name="uq_weekly_run_attempt"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), nullable=False, index=True)
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    llm_provider: Mapped[str | None] = mapped_column(String(32))
    llm_model: Mapped[str | None] = mapped_column(String(255))
    embedding_model_id: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    prep_version: Mapped[str | None] = mapped_column(String(32))
    compaction_level: Mapped[str | None] = mapped_column(String(255))
    input_token_estimate: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    input_json: Mapped[dict | None] = mapped_column(JSON)
    raw_output: Mapped[str | None] = mapped_column(Text)
    rejected_signals_json: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class WeeklyResult(Base):
    __tablename__ = "weekly_results"
    __table_args__ = (
        UniqueConstraint(
            "target_id", "week_start_date", name="uq_weekly_result_target_week"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), nullable=False, index=True)
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    latest_successful_run_id: Mapped[int | None] = mapped_column(ForeignKey("weekly_runs.id"))
    source_states: Mapped[dict] = mapped_column(JSON, nullable=False)
    exact_counts: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    data_limitations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    limited_history: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WeeklySignal(Base):
    __tablename__ = "weekly_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    result_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_results.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    connection_note: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, nullable=False)
    signal_types: Mapped[list] = mapped_column(JSON, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    configured_sources: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed: Mapped[int] = mapped_column(Integer, nullable=False)
    uncertain: Mapped[int] = mapped_column(Integer, nullable=False)
    source_states: Mapped[dict] = mapped_column(JSON, nullable=False)
    cited_event_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    claimed_sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_claim_mismatch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uses_first_observed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
