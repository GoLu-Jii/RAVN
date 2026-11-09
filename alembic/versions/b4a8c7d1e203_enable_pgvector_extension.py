"""enable pgvector on PostgreSQL

Revision ID: b4a8c7d1e203
Revises: 9a2f6d1c4b80
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op


revision: str = "b4a8c7d1e203"
down_revision: Union[str, Sequence[str], None] = "9a2f6d1c4b80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Supabase keeps contributed extensions in `extensions`, which is already
    # in its default search_path; SQLite has no extension to install.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP EXTENSION IF EXISTS vector")
