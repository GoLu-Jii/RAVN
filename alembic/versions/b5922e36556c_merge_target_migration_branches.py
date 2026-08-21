"""merge target migration branches

Revision ID: b5922e36556c
Revises: 8bd5c4430fa7, e80afbdce9d8
Create Date: 2026-08-16 09:05:41.849404

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5922e36556c'
down_revision: Union[str, Sequence[str], None] = ('8bd5c4430fa7', 'e80afbdce9d8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
