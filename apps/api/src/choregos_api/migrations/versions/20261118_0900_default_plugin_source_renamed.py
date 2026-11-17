# SPDX-License-Identifier: Apache-2.0
"""The default plugin source is renamed `tranibor` (ADR 0186)

The chart's default source of the shipped plugins went by `varga`, the name of the foundation that
published the product. The publisher is GRAAL.SYSTEMS SAS now and the source is `tranibor`
(`graalsystems/tranibor-agents`). An installation records its source by name (`org_plugins.source`); this
migration moves it, so that an upgrade finds the source the deployment declares. A deployment whose
values still declare `varga` keeps working (`PluginsConfig.source` resolves the old name).

Idempotent: a row already renamed no longer matches. The unique key is (organisation, plugin), so a
rename never collides with another row.

The downgrade gives the name back to the rows this migration renamed, which it recognises by their name
alone: a row `tranibor` installed after the upgrade comes back as `varga`, which the previous release
still reads.

Revision ID: e7b9d1f3a5c8
Revises: c8a1e3b5d7f9
Create Date: 2026-11-18 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b9d1f3a5c8"
down_revision: str | None = "c8a1e3b5d7f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "varga"
NEW = "tranibor"


def _rename(old: str, new: str) -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # Forced RLS: the migrating role sees no row without this (S39-27, S40-22e).
        connection.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    connection.execute(
        sa.text("UPDATE org_plugins SET source = :new WHERE source = :old"),
        {"old": old, "new": new},
    )


def upgrade() -> None:
    _rename(OLD, NEW)


def downgrade() -> None:
    _rename(NEW, OLD)
