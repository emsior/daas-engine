"""retention marker, magic-link nonce, listing indexes

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('run_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('report_purged_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('magic_links', schema=None) as batch_op:
        batch_op.add_column(sa.Column('nonce_hash', sa.String(length=64), nullable=True))
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index('ix_audit_events_org_id_ts_id', ['org_id', 'ts', 'id'], unique=False)
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_org_id_id', ['org_id', 'id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_org_id_id')
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_events_org_id_ts_id')
    with op.batch_alter_table('magic_links', schema=None) as batch_op:
        batch_op.drop_column('nonce_hash')
    with op.batch_alter_table('run_records', schema=None) as batch_op:
        batch_op.drop_column('report_purged_at')
