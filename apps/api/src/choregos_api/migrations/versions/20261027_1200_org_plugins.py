# SPDX-License-Identifier: Apache-2.0
"""Installed plugins per organisation (S43-04, ADR 0093): `org_plugins` and `org_plugin_files`

`org_plugins` says which plugin an organisation installed, its source, version, content digest and what
it brought; `org_plugin_files` keeps, under the same digest, the files the platform serves again later
(templates with their files, eval suites, procedures). Both under forced RLS like every other table of an
organisation. New tables, no row written: nothing to scope. The downgrade drops them, and with them the
record of what was installed; the agents and skills the plugins installed stay.

Revision ID: a7c9e1b3d5f6
Revises: e3a5c7b9d1f4
Create Date: 2026-10-27 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "a7c9e1b3d5f6"
down_revision: str | None = "e3a5c7b9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("org_plugins", "org_plugin_files")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            name,
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        )
        for name in ("created_at", "updated_at")
    ]


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id"], ["organizations.id"], name=op.f(f"fk_{table}_org_id_organizations"), ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "org_plugins",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("manifest", Json(), nullable=False),
        sa.Column("contents", Json(), nullable=False),
        sa.Column("details", Json(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("installed_by", sa.String(length=200), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        _org_fk("org_plugins"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_plugins")),
        sa.UniqueConstraint("org_id", "name", name="uq_org_plugins_org_name"),
    )
    op.create_index(op.f("ix_org_plugins_org_id"), "org_plugins", ["org_id"], unique=False)
    op.create_table(
        "org_plugin_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        *_timestamps(),
        _org_fk("org_plugin_files"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_plugin_files")),
        sa.UniqueConstraint("org_id", "digest", "path", name="uq_org_plugin_files_org_digest_path"),
    )
    op.create_index(op.f("ix_org_plugin_files_org_id"), "org_plugin_files", ["org_id"], unique=False)

    if _postgres():
        for table in TABLES:
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


def downgrade() -> None:
    for table in reversed(TABLES):
        if _postgres():
            op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.drop_index(op.f(f"ix_{table}_org_id"), table_name=table)
        op.drop_table(table)
