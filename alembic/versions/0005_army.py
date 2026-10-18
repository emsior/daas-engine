"""oauth clients/codes/tokens for MCP connectors, chat webhook on org schedules, requested period on jobs

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('oauth_clients',
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('client_name', sa.String(length=200), nullable=True),
    sa.Column('redirect_uris', sa.JSON(), nullable=False),
    sa.Column('info', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('client_id')
    )
    with op.batch_alter_table('oauth_clients', schema=None) as batch_op:
        batch_op.create_index('ix_oauth_clients_created_at', ['created_at'], unique=False)

    op.create_table('oauth_codes',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=True),
    sa.Column('user_id', sa.String(length=26), nullable=True),
    sa.Column('code_hash', sa.String(length=64), nullable=True),
    sa.Column('scopes', sa.JSON(), nullable=False),
    sa.Column('code_challenge', sa.String(length=128), nullable=False),
    sa.Column('redirect_uri', sa.String(length=512), nullable=False),
    sa.Column('redirect_uri_provided_explicitly', sa.Boolean(), nullable=False),
    sa.Column('state', sa.String(length=512), nullable=True),
    sa.Column('resource', sa.String(length=512), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('exchanged_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['oauth_clients.client_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )
    with op.batch_alter_table('oauth_codes', schema=None) as batch_op:
        batch_op.create_index('ix_oauth_codes_client_id', ['client_id'], unique=False)
        batch_op.create_index('ix_oauth_codes_expires_at', ['expires_at'], unique=False)

    op.create_table('oauth_tokens',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('org_id', sa.String(length=26), nullable=False),
    sa.Column('user_id', sa.String(length=26), nullable=True),
    sa.Column('scopes', sa.JSON(), nullable=False),
    sa.Column('resource', sa.String(length=512), nullable=True),
    sa.Column('parent_id', sa.String(length=26), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['oauth_clients.client_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('oauth_tokens', schema=None) as batch_op:
        batch_op.create_index('ix_oauth_tokens_org_id_kind', ['org_id', 'kind'], unique=False)
        batch_op.create_index('ix_oauth_tokens_client_id', ['client_id'], unique=False)

    with op.batch_alter_table('org_schedules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notify_webhook_url', sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column('notify_webhook_kind', sa.String(length=12), nullable=False,
                                      server_default='slack'))

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('period_from', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('period_to', sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('period_to')
        batch_op.drop_column('period_from')
    with op.batch_alter_table('org_schedules', schema=None) as batch_op:
        batch_op.drop_column('notify_webhook_kind')
        batch_op.drop_column('notify_webhook_url')
    with op.batch_alter_table('oauth_tokens', schema=None) as batch_op:
        batch_op.drop_index('ix_oauth_tokens_client_id')
        batch_op.drop_index('ix_oauth_tokens_org_id_kind')
    op.drop_table('oauth_tokens')
    with op.batch_alter_table('oauth_codes', schema=None) as batch_op:
        batch_op.drop_index('ix_oauth_codes_expires_at')
        batch_op.drop_index('ix_oauth_codes_client_id')
    op.drop_table('oauth_codes')
    with op.batch_alter_table('oauth_clients', schema=None) as batch_op:
        batch_op.drop_index('ix_oauth_clients_created_at')
    op.drop_table('oauth_clients')
