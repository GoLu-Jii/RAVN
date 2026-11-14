"""store versioned event embeddings for retrieval

Revision ID: d31c6a8b7e94
Revises: c8d2e1f0a4b6
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "d31c6a8b7e94"
down_revision: Union[str, Sequence[str], None] = "c8d2e1f0a4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    embedding_type = (
        Vector(384) if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    )
    op.create_table(
        "event_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("prep_version", sa.String(length=32), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "event_id", "model_id", "prep_version", name="uq_event_embedding_model_prep"
        ),
    )
    op.create_index("ix_event_embeddings_event_id", "event_embeddings", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_event_embeddings_event_id", table_name="event_embeddings")
    op.drop_table("event_embeddings")
