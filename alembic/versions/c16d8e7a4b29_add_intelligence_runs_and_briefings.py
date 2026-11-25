"""Add Company Scout run and published briefing storage."""

from alembic import op
import sqlalchemy as sa


revision = "c16d8e7a4b29"
down_revision = "ab12cd34ef56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intelligence_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("week_end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_intelligence_runs_target_id", "intelligence_runs", ["target_id"])
    op.create_index("ix_intelligence_runs_week_start_date", "intelligence_runs", ["week_start_date"])
    op.create_index("ix_intelligence_runs_status", "intelligence_runs", ["status"])

    op.create_table(
        "intelligence_briefings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("week_end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("target_id", "week_start_date", name="uq_intelligence_briefing_target_week"),
    )
    op.create_index("ix_intelligence_briefings_target_id", "intelligence_briefings", ["target_id"])
    op.create_index("ix_intelligence_briefings_week_start_date", "intelligence_briefings", ["week_start_date"])
    op.create_index("ix_intelligence_briefings_status", "intelligence_briefings", ["status"])


def downgrade() -> None:
    op.drop_index("ix_intelligence_briefings_status", table_name="intelligence_briefings")
    op.drop_index("ix_intelligence_briefings_week_start_date", table_name="intelligence_briefings")
    op.drop_index("ix_intelligence_briefings_target_id", table_name="intelligence_briefings")
    op.drop_table("intelligence_briefings")
    op.drop_index("ix_intelligence_runs_status", table_name="intelligence_runs")
    op.drop_index("ix_intelligence_runs_week_start_date", table_name="intelligence_runs")
    op.drop_index("ix_intelligence_runs_target_id", table_name="intelligence_runs")
    op.drop_table("intelligence_runs")
