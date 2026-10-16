"""run_records: period_from, period_to (zakres dat w danych runu)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('run_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('period_from', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('period_to', sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('run_records', schema=None) as batch_op:
        batch_op.drop_column('period_to')
        batch_op.drop_column('period_from')
