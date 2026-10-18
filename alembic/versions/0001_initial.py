"""initial schema: control plane (SPEC v2 2.2)

Revision ID: 0001
Revises: 
Create Date: 2026-10-09
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('magic_links',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_table('orgs',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('plan', sa.String(length=16), nullable=False),
    sa.Column('dpa_accepted_at', sa.DateTime(), nullable=True),
    sa.Column('dpa_version', sa.String(length=16), nullable=True),
    sa.Column('locale', sa.String(length=8), nullable=False),
    sa.Column('retention_days', sa.Integer(), nullable=False),
    sa.Column('llm_narrative', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('locale', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('api_keys',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('prefix', sa.String(length=8), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('scopes', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=26), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('prefix')
    )
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.create_index('ix_api_keys_org_id', ['org_id'], unique=False)

    op.create_table('audit_events',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=True),
    sa.Column('actor_type', sa.String(length=8), nullable=False),
    sa.Column('actor_id', sa.String(length=26), nullable=True),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('target', sa.String(length=80), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index('ix_audit_events_org_id_id', ['org_id', 'id'], unique=False)

    op.create_table('invitations',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('role', sa.String(length=8), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('invited_by', sa.String(length=26), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('accepted_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.create_index('ix_invitations_email', ['email'], unique=False)
        batch_op.create_index('ix_invitations_org_id', ['org_id'], unique=False)

    op.create_table('memberships',
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('user_id', sa.String(length=26), nullable=False),
    sa.Column('role', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('org_id', 'user_id')
    )
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.create_index('ix_memberships_user_id', ['user_id'], unique=False)

    op.create_table('run_records',
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('pipeline', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('job_id', sa.String(length=26), nullable=True),
    sa.Column('sha256_html', sa.String(length=64), nullable=True),
    sa.Column('sha256_md', sa.String(length=64), nullable=True),
    sa.Column('sha256_json', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('run_id')
    )
    with op.batch_alter_table('run_records', schema=None) as batch_op:
        batch_op.create_index('ix_run_records_org_id_created_at', ['org_id', 'created_at'], unique=False)
        batch_op.create_index('ix_run_records_org_sha_html', ['org_id', 'sha256_html'], unique=False)
        batch_op.create_index('ix_run_records_org_sha_json', ['org_id', 'sha256_json'], unique=False)
        batch_op.create_index('ix_run_records_org_sha_md', ['org_id', 'sha256_md'], unique=False)

    op.create_table('sessions',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=26), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=True),
    sa.Column('csrf_token', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.create_index('ix_sessions_user_id', ['user_id'], unique=False)

    op.create_table('uploads',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('filename', sa.String(length=128), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('path', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('retention_until', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.create_index('ix_uploads_org_id', ['org_id'], unique=False)

    op.create_table('usage_counters',
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('period', sa.String(length=7), nullable=False),
    sa.Column('runs', sa.Integer(), nullable=False),
    sa.Column('upload_bytes', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('org_id', 'period')
    )
    op.create_table('webhooks',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('secret_hash', sa.String(length=64), nullable=False),
    sa.Column('events', sa.JSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'url', name='uq_webhooks_org_url')
    )
    with op.batch_alter_table('webhooks', schema=None) as batch_op:
        batch_op.create_index('ix_webhooks_org_id', ['org_id'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('pipeline', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('message', sa.String(length=200), nullable=True),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('upload_id', sa.String(length=32), nullable=True),
    sa.Column('client_name', sa.String(length=200), nullable=True),
    sa.Column('force_mock', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('error_code', sa.String(length=40), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_org_id_created_at', ['org_id', 'created_at'], unique=False)
        batch_op.create_index('ix_jobs_status', ['status'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_status')
        batch_op.drop_index('ix_jobs_org_id_created_at')

    op.drop_table('jobs')
    with op.batch_alter_table('webhooks', schema=None) as batch_op:
        batch_op.drop_index('ix_webhooks_org_id')

    op.drop_table('webhooks')
    op.drop_table('usage_counters')
    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.drop_index('ix_uploads_org_id')

    op.drop_table('uploads')
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.drop_index('ix_sessions_user_id')

    op.drop_table('sessions')
    with op.batch_alter_table('run_records', schema=None) as batch_op:
        batch_op.drop_index('ix_run_records_org_sha_md')
        batch_op.drop_index('ix_run_records_org_sha_json')
        batch_op.drop_index('ix_run_records_org_sha_html')
        batch_op.drop_index('ix_run_records_org_id_created_at')

    op.drop_table('run_records')
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.drop_index('ix_memberships_user_id')

    op.drop_table('memberships')
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.drop_index('ix_invitations_org_id')
        batch_op.drop_index('ix_invitations_email')

    op.drop_table('invitations')
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_events_org_id_id')

    op.drop_table('audit_events')
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_index('ix_api_keys_org_id')

    op.drop_table('api_keys')
    op.drop_table('users')
    op.drop_table('orgs')
    op.drop_table('magic_links')
