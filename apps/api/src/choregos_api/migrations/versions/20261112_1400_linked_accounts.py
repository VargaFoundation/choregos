# SPDX-License-Identifier: Apache-2.0
"""Linked accounts: `tracker_identities` becomes `linked_accounts` (S66-03, ADR 0154)

A member's account on a code forge, by provider and instance (`provider`, `base_url`), with what the forge
says of it (`login`, `avatar_url`, `profile_url`) and whether the member proved it (`verified`,
`verified_at`, the `connector` whose OAuth application proved it). Every `tracker_identities` row is
copied: `github` at `https://github.com`, NOT verified (an administrator declared it, ADR 0102), with its
id, author and timestamps. Under forced RLS with the same organisation policy the old table had.

The downgrade copies back the `github` links of `https://github.com`, verified or not, and drops the
rest: GitLab and Forgejo links, and GitHub Enterprise ones, did not exist before.

Revision ID: c7e2a9d4f1b6
Revises: f9b1d3e5a7c0
Create Date: 2026-11-12 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e2a9d4f1b6"
down_revision: str | None = "f9b1d3e5a7c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW = "linked_accounts"
OLD = "tracker_identities"
GITHUB_COM = "https://github.com"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    ]


def _owned(table: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f(f"fk_{table}_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f(f"fk_{table}_user_id_users"), ondelete="CASCADE"),
    ]


def _protect(table: str) -> None:
    for column in ("org_id", "user_id"):
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column], unique=False)
    if _postgres():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_org_isolation ON {table}
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def _drop(table: str) -> None:
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    for column in ("user_id", "org_id"):
        op.drop_index(op.f(f"ix_{table}_{column}"), table_name=table)
    op.drop_table(table)


def upgrade() -> None:
    op.create_table(
        NEW,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("login", sa.String(length=255), nullable=False),
        sa.Column("avatar_url", sa.String(length=1024), nullable=True),
        sa.Column("profile_url", sa.String(length=1024), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connector", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        *_timestamps(),
        *_owned(NEW),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{NEW}")),
        sa.UniqueConstraint("org_id", "provider", "base_url", "external_id", name="uq_linked_accounts_external"),
        sa.UniqueConstraint("org_id", "user_id", "provider", "base_url", name="uq_linked_accounts_member"),
    )
    _protect(NEW)
    # The migration runs as the owner: forced RLS still applies to it, so the copy is made under the
    # every-organisation scope the policies honour (as the other data steps do).
    if _postgres():
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
    op.execute(
        sa.text(
            f"INSERT INTO {NEW} (id, org_id, user_id, provider, base_url, external_id, login, verified, "
            "created_by, created_at, updated_at) "
            f"SELECT id, org_id, user_id, tracker, :base, external_id, login, :verified, created_by, "
            f"created_at, updated_at FROM {OLD} WHERE tracker = 'github'"
        ).bindparams(base=GITHUB_COM, verified=False)
    )
    _drop(OLD)


def downgrade() -> None:
    op.create_table(
        OLD,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("tracker", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("login", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        *_timestamps(),
        *_owned(OLD),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{OLD}")),
        sa.UniqueConstraint("org_id", "tracker", "external_id", name="uq_tracker_identities_external"),
        sa.UniqueConstraint("org_id", "user_id", "tracker", name="uq_tracker_identities_member"),
    )
    _protect(OLD)
    if _postgres():
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
    op.execute(
        sa.text(
            f"INSERT INTO {OLD} (id, org_id, user_id, tracker, external_id, login, created_by, created_at, "
            f"updated_at) SELECT id, org_id, user_id, provider, external_id, login, created_by, created_at, "
            f"updated_at FROM {NEW} WHERE provider = 'github' AND base_url = :base"
        ).bindparams(base=GITHUB_COM)
    )
    _drop(NEW)
