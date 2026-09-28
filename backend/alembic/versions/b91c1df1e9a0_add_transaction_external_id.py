"""add transaction external id

Revision ID: b91c1df1e9a0
Revises: d1a6f4c8b729
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b91c1df1e9a0"
down_revision: Union[str, None] = "d1a6f4c8b729"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_transactions_external_id", "transactions", ["external_id"])


def downgrade() -> None:
    op.drop_constraint("uq_transactions_external_id", "transactions", type_="unique")
    op.drop_column("transactions", "external_id")
