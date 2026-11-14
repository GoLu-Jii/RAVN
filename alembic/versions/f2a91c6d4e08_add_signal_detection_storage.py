"""add Signal Detector run and signal storage

Revision ID: f2a91c6d4e08
Revises: d7e4a6b91c32
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a91c6d4e08"
down_revision: Union[str, Sequence[str], None] = "d7e4a6b91c32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "signal_detection_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("targets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("llm_provider", sa.String(length=32), nullable=True),
        sa.Column("llm_model", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("input_size_bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "target_id",
            "week_start_date",
            "attempt",
            name="uq_signal_detection_run_attempt",
        ),
    )
    op.create_index(
        "ix_signal_detection_runs_target_id", "signal_detection_runs", ["target_id"]
    )
    op.create_index(
        "ix_signal_detection_runs_week_start_date",
        "signal_detection_runs",
        ["week_start_date"],
    )

    op.create_table(
        "detected_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("signal_detection_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("targets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("signal_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("signal_types", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("confidence_rationale", sa.Text(), nullable=False),
        sa.Column("current_lead_ids", sa.JSON(), nullable=False),
        sa.Column("historical_lead_ids", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "run_id", "signal_index", name="uq_detected_signal_run_index"
        ),
    )
    op.create_index("ix_detected_signals_run_id", "detected_signals", ["run_id"])
    op.create_index(
        "ix_detected_signals_target_id", "detected_signals", ["target_id"]
    )
    op.create_index(
        "ix_detected_signals_week_start_date",
        "detected_signals",
        ["week_start_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_detected_signals_week_start_date", table_name="detected_signals")
    op.drop_index("ix_detected_signals_target_id", table_name="detected_signals")
    op.drop_index("ix_detected_signals_run_id", table_name="detected_signals")
    op.drop_table("detected_signals")
    op.drop_index(
        "ix_signal_detection_runs_week_start_date",
        table_name="signal_detection_runs",
    )
    op.drop_index(
        "ix_signal_detection_runs_target_id", table_name="signal_detection_runs"
    )
    op.drop_table("signal_detection_runs")
