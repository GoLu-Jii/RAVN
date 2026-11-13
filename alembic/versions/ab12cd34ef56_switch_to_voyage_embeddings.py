"""Switch event vectors from local 384-d to Voyage 256-d embeddings.

Revision ID: ab12cd34ef56
Revises: f9c2a1d7e604
Create Date: 2026-11-10
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "ab12cd34ef56"
down_revision = "f9c2a1d7e604"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Vectors are derived data. Clear old-model vectors so no incompatible
    # 384-dimensional rows can be read as Voyage vectors after the switch.
    op.execute(sa.text("DELETE FROM event_embeddings"))
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "event_embeddings",
            "embedding",
            type_=Vector(256),
            existing_type=Vector(384),
            existing_nullable=False,
            postgresql_using="embedding::vector(256)",
        )


def downgrade() -> None:
    # Voyage vectors cannot be converted back into the previous model's space.
    # Remove only derived vectors; events remain and can be re-embedded.
    op.execute(sa.text("DELETE FROM event_embeddings"))
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "event_embeddings",
            "embedding",
            type_=Vector(384),
            existing_type=Vector(256),
            existing_nullable=False,
            postgresql_using="embedding::vector(384)",
        )
