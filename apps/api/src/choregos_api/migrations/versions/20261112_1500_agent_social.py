# SPDX-License-Identifier: Apache-2.0
"""Watching, starring and the inbox items of an agent (ADR 0152): `agent_watches`, `agent_stars`,
`agent_notifications`

All three under forced row-level security on the organisation, like the other tables that carry one.
`agent_notifications` has, in addition, RESTRICTIVE policies that let only the recipient (or the
platform scope) read, change or delete an item: writing one for a watcher is the actor's gesture, so
inserts stay under the organisation's policy alone. Every existing agent's owner starts watching it.
The downgrade drops the three tables and loses who watches and stars what.

Revision ID: a2c4e6f8b0d1
Revises: f1b3d5e7a9c2
Create Date: 2026-11-12 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json, utcnow, uuid7

revision: str = "a2c4e6f8b0d1"
down_revision: str | None = "f1b3d5e7a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("agent_watches", "agent_stars", "agent_notifications")
ORG_POLICY = """
    USING (
      '*' = ANY(choregos_current_orgs())
      OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
    )
"""
OWNER_CONDITION = "'*' = ANY(choregos_current_orgs()) OR user_id = choregos_utilisateur_courant()"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _base(table: str) -> list[sa.schema.SchemaItem]:
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f(f"fk_{table}_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _person_and_agent(table: str) -> list[sa.schema.SchemaItem]:
    return [
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f(f"fk_{table}_agent_id_agents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f(f"fk_{table}_user_id_users"), ondelete="CASCADE"
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "agent_watches",
        *_base("agent_watches"),
        *_person_and_agent("agent_watches"),
        sa.Column("level", sa.String(length=16), server_default="all", nullable=False),
        sa.UniqueConstraint("agent_id", "user_id", name="uq_agent_watches_agent_user"),
    )
    op.create_table(
        "agent_stars",
        *_base("agent_stars"),
        *_person_and_agent("agent_stars"),
        sa.UniqueConstraint("agent_id", "user_id", name="uq_agent_stars_agent_user"),
    )
    op.create_table(
        "agent_notifications",
        *_base("agent_notifications"),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("related_agent_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=True),
        sa.Column("detail", Json(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_agent_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_notifications_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["related_agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_notifications_related_agent_id_agents"),
            ondelete="CASCADE",
        ),
    )
    for table in TABLES:
        op.create_index(op.f(f"ix_{table}_org_id"), table, ["org_id"], unique=False)
    for table in ("agent_watches", "agent_stars"):
        op.create_index(op.f(f"ix_{table}_agent_id"), table, ["agent_id"], unique=False)
        op.create_index(op.f(f"ix_{table}_user_id"), table, ["user_id"], unique=False)
    op.create_index(op.f("ix_agent_notifications_agent_id"), "agent_notifications", ["agent_id"], unique=False)
    op.create_index("ix_agent_notifications_user_read", "agent_notifications", ["user_id", "read_at"])
    if _postgres():
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY {table}_org_isolation ON {table} {ORG_POLICY}")
        # An item is its recipient's: nobody else reads, marks or deletes it (inserts are the actor's).
        for verb in ("SELECT", "UPDATE", "DELETE"):
            op.execute(
                f"CREATE POLICY agent_notifications_owner_{verb.lower()} ON agent_notifications "
                f"AS RESTRICTIVE FOR {verb} USING ({OWNER_CONDITION})"
            )
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
    _backfill_owners()


def _backfill_owners() -> None:
    """Every agent that has an owner is watched by it, as a new agent now is."""
    bind = op.get_bind()
    watches = sa.table(
        "agent_watches",
        sa.column("id", sa.String),
        sa.column("org_id", sa.String),
        sa.column("agent_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("level", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = utcnow()
    rows = bind.execute(
        sa.text("SELECT id, org_id, owner_id FROM agents WHERE owner_id IS NOT NULL")
    ).fetchall()
    if rows:
        op.bulk_insert(
            watches,
            [
                {
                    "id": uuid7(),
                    "org_id": org_id,
                    "agent_id": agent_id,
                    "user_id": owner_id,
                    "level": "all",
                    "created_at": now,
                    "updated_at": now,
                }
                for agent_id, org_id, owner_id in rows
            ],
        )


def downgrade() -> None:
    if _postgres():
        for verb in ("select", "update", "delete"):
            op.execute(f"DROP POLICY IF EXISTS agent_notifications_owner_{verb} ON agent_notifications")
        for table in TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    op.drop_table("agent_notifications")
    op.drop_table("agent_stars")
    op.drop_table("agent_watches")
