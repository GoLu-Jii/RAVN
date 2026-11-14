"""persist weekly analysis attempts, current results and signals

Revision ID: e5a1c7d9b304
Revises: d31c6a8b7e94
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a1c7d9b304"
down_revision: Union[str, Sequence[str], None] = "d31c6a8b7e94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "weekly_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("llm_provider", sa.String(length=32)),
        sa.Column("llm_model", sa.String(length=255)),
        sa.Column("embedding_model_id", sa.String(length=255)),
        sa.Column("prompt_version", sa.String(length=64)),
        sa.Column("prep_version", sa.String(length=32)),
        sa.Column("compaction_level", sa.String(length=255)),
        sa.Column("input_token_estimate", sa.Integer()),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("input_json", sa.JSON()),
        sa.Column("raw_output", sa.Text()),
        sa.Column("rejected_signals_json", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("target_id", "week_start_date", "attempt", name="uq_weekly_run_attempt"),
    )
    op.create_index("ix_weekly_runs_target_id", "weekly_runs", ["target_id"])
    op.create_index("ix_weekly_runs_week_start_date", "weekly_runs", ["week_start_date"])

    op.create_table(
        "weekly_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("latest_successful_run_id", sa.Integer(), sa.ForeignKey("weekly_runs.id")),
        sa.Column("source_states", sa.JSON(), nullable=False),
        sa.Column("exact_counts", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("data_limitations", sa.JSON(), nullable=False),
        sa.Column("limited_history", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("target_id", "week_start_date", name="uq_weekly_result_target_week"),
    )
    op.create_index("ix_weekly_results_target_id", "weekly_results", ["target_id"])
    op.create_index("ix_weekly_results_week_start_date", "weekly_results", ["week_start_date"])

    op.create_table(
        "weekly_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("result_id", sa.Integer(), sa.ForeignKey("weekly_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("connection_note", sa.Text(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("signal_types", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("configured_sources", sa.Integer(), nullable=False),
        sa.Column("confirmed", sa.Integer(), nullable=False),
        sa.Column("uncertain", sa.Integer(), nullable=False),
        sa.Column("source_states", sa.JSON(), nullable=False),
        sa.Column("cited_event_ids", sa.JSON(), nullable=False),
        sa.Column("uses_first_observed", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_weekly_signals_result_id", "weekly_signals", ["result_id"])


def downgrade() -> None:
    op.drop_index("ix_weekly_signals_result_id", table_name="weekly_signals")
    op.drop_table("weekly_signals")
    op.drop_index("ix_weekly_results_week_start_date", table_name="weekly_results")
    op.drop_index("ix_weekly_results_target_id", table_name="weekly_results")
    op.drop_table("weekly_results")
    op.drop_index("ix_weekly_runs_week_start_date", table_name="weekly_runs")
    op.drop_index("ix_weekly_runs_target_id", table_name="weekly_runs")
    op.drop_table("weekly_runs")
