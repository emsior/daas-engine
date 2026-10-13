"""org schedules: weekly reminder, auto-run on upload, notification on finish

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('org_schedules',
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('reminder_enabled', sa.Boolean(), nullable=False),
    sa.Column('reminder_weekday', sa.Integer(), nullable=False),
    sa.Column('reminder_hour', sa.Integer(), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('reminder_skip_if_recent_upload', sa.Boolean(), nullable=False),
    sa.Column('auto_run_on_upload', sa.Boolean(), nullable=False),
    sa.Column('notify_on_finish', sa.Boolean(), nullable=False),
    sa.Column('next_reminder_at', sa.DateTime(), nullable=True),
    sa.Column('last_reminder_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('org_id')
    )
    with op.batch_alter_table('org_schedules', schema=None) as batch_op:
        batch_op.create_index('ix_org_schedules_due', ['reminder_enabled', 'next_reminder_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('org_schedules', schema=None) as batch_op:
        batch_op.drop_index('ix_org_schedules_due')
    op.drop_table('org_schedules')
