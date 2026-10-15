"""OAuth grants and refresh-token rotation history.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15
"""
from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_grants",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("resource", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revocation_reason", sa.String(length=32), nullable=True),
        sa.Column("lock_seq", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["oauth_clients.client_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("oauth_tokens", schema=None) as batch_op:
        batch_op.add_column(sa.Column("grant_id", sa.String(length=26), nullable=True))
        batch_op.add_column(sa.Column("rotated_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(
            "fk_oauth_tokens_grant_id_oauth_grants", "oauth_grants", ["grant_id"], ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_oauth_tokens_grant_id", ["grant_id"], unique=False)

    # Existing connections must be authorized again; legacy tokens receive no grant.
    tokens = sa.table("oauth_tokens", sa.column("revoked_at", sa.DateTime()))
    op.execute(tokens.update().where(tokens.c.revoked_at.is_(None)).values(
        revoked_at=datetime.now(UTC).replace(tzinfo=None),
    ))


def downgrade() -> None:
    with op.batch_alter_table("oauth_tokens", schema=None) as batch_op:
        batch_op.drop_index("ix_oauth_tokens_grant_id")
        batch_op.drop_constraint("fk_oauth_tokens_grant_id_oauth_grants", type_="foreignkey")
        batch_op.drop_column("rotated_at")
        batch_op.drop_column("grant_id")
    op.drop_table("oauth_grants")
