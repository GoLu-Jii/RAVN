"""merge weekly-lead and signal-storage migration heads

Revision ID: e36bbec8d214
Revises: c72f8a0b91de, f2a91c6d4e08
Create Date: 2026-11-15
"""
from typing import Sequence, Union


revision: str = "e36bbec8d214"
down_revision: Union[str, Sequence[str], None] = (
    "c72f8a0b91de",
    "f2a91c6d4e08",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
