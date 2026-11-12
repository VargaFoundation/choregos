# SPDX-License-Identifier: Apache-2.0
"""Outposts: `outposts`, `outpost_instances`, `outpost_enrolments`, `outpost_calls` (S65-11, ADRs 0147-0149)

An outpost is a place a person enrols, of one organisation, that only dials out. Four tables, and
`org_connectors.outpost_id`, the reference of an `mcp-outpost` connector to the outpost it routes to
(`RESTRICT`: an outpost that still has connectors is not deleted).

All four are under the organisation's forced row-level security, like every tenant table, and a
second setting narrows them, `app.current_outpost` (ADR 0148 §6), read by `choregos_current_outpost()`:

- `outpost_instances`, `outpost_enrolments` and `outpost_calls` are an outpost's own rows: visible and
  writable only to a session that names that outpost, inside an organisation scope that holds it. A
  session that names none sees none of them, whatever its organisation scope, `*` included;
- `outposts` is the organisation's registry: a session that names no outpost reads the outposts of
  its organisations (the administrator's list), and one that names an outpost reads that row only.

A policy without `WITH CHECK` judges writes with the same expression: a row can be written only where
it could be read. New tables, no row written: nothing to scope. The downgrade drops them and the
column, and with them every outpost, instance, token and call; connectors of the type stay, without
their reference.

Revision ID: c6e8a0b2d4f7
Revises: a7f7c0f0e0d7
Create Date: 2026-11-12 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "c6e8a0b2d4f7"
down_revision: str | None = "a7f7c0f0e0d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: An outpost's own rows: the outpost setting is required, and fails closed.
OUTPOST_ROWS = ("outpost_instances", "outpost_enrolments", "outpost_calls")
ORGANISATION = (
    "('*' = ANY(choregos_current_orgs()) OR choregos_org_slug(org_id) = ANY(choregos_current_orgs()))"
)


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def _owned(table: str) -> list[sa.ForeignKeyConstraint]:
    """The organisation and the outpost a row belongs to: it goes with either."""
    return [
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f(f"fk_{table}_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["outpost_id"], ["outposts.id"], name=op.f(f"fk_{table}_outpost_id_outposts"), ondelete="CASCADE"
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "outposts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("capabilities", Json(), nullable=False),
        sa.Column("epoch", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_outposts_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outposts")),
        sa.UniqueConstraint("org_id", "slug", name="uq_outposts_org_slug"),
    )
    op.create_index(op.f("ix_outposts_org_id"), "outposts", ["org_id"], unique=False)

    op.create_table(
        "outpost_instances",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("outpost_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("signing_key", sa.String(length=43), nullable=False),
        sa.Column("sealing_key", sa.String(length=43), nullable=False),
        sa.Column("fingerprint", sa.String(length=50), nullable=False),
        sa.Column("facts", Json(), nullable=False),
        sa.Column("daemon_version", sa.String(length=64), nullable=False),
        sa.Column("protocol", sa.Integer(), server_default="1", nullable=False),
        sa.Column("capabilities", Json(), nullable=False),
        sa.Column("endpoints", Json(), nullable=False),
        # 64 bits: a daemon's sequence follows its clock in milliseconds, up to 2^53 - 1.
        sa.Column("sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=200), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_owned("outpost_instances"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outpost_instances")),
        # Across the installation, below row-level security: a key enrolled anywhere is refused elsewhere.
        sa.UniqueConstraint("signing_key", name="uq_outpost_instances_signing_key"),
    )
    op.create_index(op.f("ix_outpost_instances_org_id"), "outpost_instances", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_outpost_instances_outpost_id"), "outpost_instances", ["outpost_id"], unique=False
    )

    op.create_table(
        "outpost_enrolments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("outpost_id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instance_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        *_timestamps(),
        *_owned("outpost_enrolments"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outpost_enrolments")),
        sa.UniqueConstraint("token_digest", name="uq_outpost_enrolments_token_digest"),
    )
    op.create_index(op.f("ix_outpost_enrolments_org_id"), "outpost_enrolments", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_outpost_enrolments_outpost_id"), "outpost_enrolments", ["outpost_id"], unique=False
    )

    op.create_table(
        "outpost_calls",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("outpost_id", sa.String(length=36), nullable=False),
        sa.Column("connector_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("endpoint", sa.String(length=63), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=True),
        sa.Column("write", sa.Boolean(), nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=True),
        sa.Column("epoch", sa.Integer(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # What the door recorded, once: an answer or a refusal, from which instance, under which epoch.
        sa.Column("recorded", sa.String(length=8), nullable=True),
        sa.Column("recorded_by", sa.String(length=64), nullable=True),
        sa.Column("recorded_epoch", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=True),
        sa.Column("refusal", sa.String(length=32), nullable=True),
        sa.Column("arguments_sha256", sa.String(length=64), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=200), nullable=True),
        sa.Column("action_id", sa.String(length=36), nullable=True),
        # The arguments while they wait, sealed as a row of `org_secrets` is (ADR 0103, ADR 0148 §11).
        sa.Column("arguments_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("arguments_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("arguments_wrapped_dek", sa.LargeBinary(), nullable=True),
        sa.Column("arguments_dek_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("arguments_kek_id", sa.String(length=32), nullable=True),
        *_timestamps(),
        *_owned("outpost_calls"),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["org_connectors.id"],
            name=op.f("fk_outpost_calls_connector_id_org_connectors"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["outpost_instances.id"],
            name=op.f("fk_outpost_calls_instance_id_outpost_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_outpost_calls_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outpost_calls")),
    )
    op.create_index(op.f("ix_outpost_calls_org_id"), "outpost_calls", ["org_id"], unique=False)
    op.create_index(op.f("ix_outpost_calls_connector_id"), "outpost_calls", ["connector_id"], unique=False)
    op.create_index(op.f("ix_outpost_calls_instance_id"), "outpost_calls", ["instance_id"], unique=False)
    op.create_index(op.f("ix_outpost_calls_project_id"), "outpost_calls", ["project_id"], unique=False)
    op.create_index("ix_outpost_calls_outpost_state", "outpost_calls", ["outpost_id", "state"], unique=False)

    with op.batch_alter_table("org_connectors") as batch:
        batch.add_column(sa.Column("outpost_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_org_connectors_outpost_id_outposts", "outposts", ["outpost_id"], ["id"], ondelete="RESTRICT"
        )
        batch.create_index("ix_org_connectors_outpost_id", ["outpost_id"])

    if not _postgres():
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION choregos_current_outpost() RETURNS text AS $$
          SELECT nullif(current_setting('app.current_outpost', true), '')
        $$ LANGUAGE sql STABLE;
        """
    )
    for table in ("outposts", *OUTPOST_ROWS):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY outposts_org_isolation ON outposts
        USING (
          {ORGANISATION}
          AND (choregos_current_outpost() IS NULL OR id = choregos_current_outpost())
        )
        """
    )
    for table in OUTPOST_ROWS:
        # `outpost_id = NULL` is never true: a session that names no outpost sees no row.
        op.execute(
            f"""
            CREATE POLICY {table}_org_isolation ON {table}
            USING (
              {ORGANISATION}
              AND outpost_id = choregos_current_outpost()
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        for table in (*OUTPOST_ROWS, "outposts"):
            op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    with op.batch_alter_table("org_connectors") as batch:
        batch.drop_index("ix_org_connectors_outpost_id")
        batch.drop_constraint("fk_org_connectors_outpost_id_outposts", type_="foreignkey")
        batch.drop_column("outpost_id")
    op.drop_index("ix_outpost_calls_outpost_state", table_name="outpost_calls")
    for column in ("project_id", "instance_id", "connector_id", "org_id"):
        op.drop_index(op.f(f"ix_outpost_calls_{column}"), table_name="outpost_calls")
    op.drop_table("outpost_calls")
    for table in ("outpost_enrolments", "outpost_instances"):
        op.drop_index(op.f(f"ix_{table}_outpost_id"), table_name=table)
        op.drop_index(op.f(f"ix_{table}_org_id"), table_name=table)
        op.drop_table(table)
    op.drop_index(op.f("ix_outposts_org_id"), table_name="outposts")
    op.drop_table("outposts")
    if _postgres():
        op.execute("DROP FUNCTION IF EXISTS choregos_current_outpost()")
