# SPDX-License-Identifier: Apache-2.0
"""Agent release channels (S40-02a, ADR 0071): production and testing pointers, project pins by channel

An agent gets two pointers at its immutable revisions — `stable_version` (production) and
`candidate_version` (testing, with a canary share) — and a release policy. Every existing agent is
given `stable_version = max(version)` so that nothing changes for anyone who does nothing, and one
`baseline` row in `agent_releases` (the rollback oracle: under the old rule every version was
"latest" once). A project can follow testing (`projects.agent_channel`) and a pin is a fixed revision
XOR a channel. `agent_releases` is under forced row-level security like the registry's tables.

Revision ID: c1e3a5b7d9f2
Revises: d7f0a2c4e6b8
Create Date: 2026-10-17 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json, uuid7

revision: str = "c1e3a5b7d9f2"
down_revision: str | None = "d7f0a2c4e6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "agent_releases"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # The migration job runs as the application role, under forced RLS: without this, every
        # data step below sees zero rows (S39-27).
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("stable_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("candidate_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("candidate_percent", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("candidate_since", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("release_policy", Json(), nullable=True))
        batch.create_check_constraint(op.f("ck_agents_candidate_percent"), "candidate_percent BETWEEN 0 AND 100")
    op.execute(
        "UPDATE agents SET stable_version = "
        "(SELECT max(version) FROM agent_versions WHERE agent_versions.agent_id = agents.id)"
    )
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("agent_channel", sa.String(length=16), server_default="stable", nullable=False))
    with op.batch_alter_table("project_agents") as batch:
        batch.alter_column("version", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("channel", sa.String(length=16), nullable=True))
        batch.create_check_constraint(op.f("ck_project_agents_version_or_channel"), "(version IS NULL) <> (channel IS NULL)")

    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("percent", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requested_by", sa.String(length=200), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("checks", Json(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_agent_releases_agent_id_agents"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_agent_releases_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_releases")),
    )
    op.create_index(op.f("ix_agent_releases_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_agent_releases_agent_id"), TABLE, ["agent_id"], unique=False)
    op.create_index("ix_agent_releases_agent_created", TABLE, ["agent_id", "created_at"], unique=False)

    if _postgres():
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {TABLE}_org_isolation ON {TABLE}
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )

    # The baseline: production is where every agent is today, and every version up to there was
    # "latest" once — a rollback may go back to any of them.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, org_id, stable_version FROM agents WHERE stable_version IS NOT NULL")).all()
    if rows:
        now = datetime.now(UTC)
        releases = sa.table(
            TABLE,
            sa.column("id"),
            sa.column("org_id"),
            sa.column("agent_id"),
            sa.column("channel"),
            sa.column("kind"),
            sa.column("version"),
            sa.column("status"),
            sa.column("requested_by"),
            sa.column("checks", Json()),
            sa.column("done_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(
            releases,
            [
                {
                    "id": uuid7(),
                    "org_id": row.org_id,
                    "agent_id": row.id,
                    "channel": "stable",
                    "kind": "baseline",
                    "version": row.stable_version,
                    "status": "done",
                    "requested_by": "migration",
                    "checks": [],
                    "done_at": now,
                }
                for row in rows
            ],
        )


def downgrade() -> None:
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    op.drop_index("ix_agent_releases_agent_created", table_name=TABLE)
    op.drop_index(op.f("ix_agent_releases_agent_id"), table_name=TABLE)
    op.drop_index(op.f("ix_agent_releases_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
    op.execute("DELETE FROM project_agents WHERE version IS NULL")
    with op.batch_alter_table("project_agents") as batch:
        batch.drop_constraint(op.f("ck_project_agents_version_or_channel"), type_="check")
        batch.drop_column("channel")
        batch.alter_column("version", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("agent_channel")
    with op.batch_alter_table("agents") as batch:
        batch.drop_constraint(op.f("ck_agents_candidate_percent"), type_="check")
        batch.drop_column("release_policy")
        batch.drop_column("candidate_since")
        batch.drop_column("candidate_percent")
        batch.drop_column("candidate_version")
        batch.drop_column("stable_version")
