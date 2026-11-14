"""add weekly lead runs and vector storage

Revision ID: d7e4a6b91c32
Revises: ab12cd34ef56
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "d7e4a6b91c32"
down_revision: Union[str, Sequence[str], None] = "ab12cd34ef56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "weekly_lead_runs",
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
        sa.Column("input_event_ids", sa.JSON(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("llm_provider", sa.String(length=32), nullable=True),
        sa.Column("llm_model", sa.String(length=255), nullable=True),
        sa.Column("embedding_model_id", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("input_token_estimate", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "target_id", "week_start_date", "attempt",
            name="uq_weekly_lead_run_attempt",
        ),
    )
    op.create_index("ix_weekly_lead_runs_target_id", "weekly_lead_runs", ["target_id"])
    op.create_index(
        "ix_weekly_lead_runs_week_start_date", "weekly_lead_runs", ["week_start_date"]
    )

    embedding_type = (
        Vector(256) if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    )
    op.create_table(
        "weekly_leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("weekly_lead_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("targets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("lead_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_event_ids", sa.JSON(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("embedding_model_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "lead_index", name="uq_weekly_lead_run_index"),
    )
    op.create_index("ix_weekly_leads_run_id", "weekly_leads", ["run_id"])
    op.create_index("ix_weekly_leads_target_id", "weekly_leads", ["target_id"])
    op.create_index("ix_weekly_leads_week_start_date", "weekly_leads", ["week_start_date"])


def downgrade() -> None:
    op.drop_index("ix_weekly_leads_week_start_date", table_name="weekly_leads")
    op.drop_index("ix_weekly_leads_target_id", table_name="weekly_leads")
    op.drop_index("ix_weekly_leads_run_id", table_name="weekly_leads")
    op.drop_table("weekly_leads")
    op.drop_index("ix_weekly_lead_runs_week_start_date", table_name="weekly_lead_runs")
    op.drop_index("ix_weekly_lead_runs_target_id", table_name="weekly_lead_runs")
    op.drop_table("weekly_lead_runs")
