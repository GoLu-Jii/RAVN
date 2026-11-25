"""Add a manually supplied YouTube channel URL to targets."""

from alembic import op
import sqlalchemy as sa


revision = "d4b19f6e2a73"
down_revision = ("c16d8e7a4b29", "e36bbec8d214")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("targets", sa.Column("youtube_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("targets", "youtube_url")
