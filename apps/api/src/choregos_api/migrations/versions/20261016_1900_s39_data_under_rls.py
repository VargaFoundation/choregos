# SPDX-License-Identifier: Apache-2.0
"""Replay the data steps of three S39 migrations, this time under the RLS bypass (S39-27)

Under PostgreSQL the tables are under forced RLS and the migration job runs as the application
role: without `set_config('app.current_orgs', '*', true)` every statement on them sees zero rows,
and the job still ends green. Three S39 migrations forgot it, so on a database that had data their
backfills did nothing — measured on the dev tenant on 2026-10-16:

- `5b7d9f1a3c4e` (agent scopes, S39-09): catalogue agents stayed `org` instead of `platform`;
- `d4f6a8c0e2b4` (egress allowlist, S39-22): no host already in use was seeded;
- `f3a5c7e9b1d2` (template provenance, S39-25): `varga/prod-verifier` kept its `template:*` author.

The three migrations are fixed for the databases that have not run them yet; this one replays their
data steps for those that have. Each step is idempotent: the scope backfill only touches catalogue
agents, the egress seed leaves alone an (organisation, host) that already has a live rule, and the
provenance backfill only matches authors still `template:*`. No downgrade: the data is only made
more precise.

Revision ID: a7c9e1b3d5f8
Revises: f3a5c7e9b1d2
Create Date: 2026-10-16 19:00:00+00:00
"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import Sequence
from types import ModuleType

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1b3d5f8"
down_revision: str | None = "f3a5c7e9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HERE = pathlib.Path(__file__).resolve().parent


def _migration(filename: str) -> ModuleType:
    """A sibling migration, loaded by path (migration files are not an importable package)."""
    spec = importlib.util.spec_from_file_location(f"_s39_27_{filename[:-3]}", HERE / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    scopes = _migration("20261015_1200_agent_scopes.py")
    bind.execute(sa.text(scopes.BACKFILL))
    _migration("20261017_0900_org_egress_rules.py")._seed(skip_existing=True)
    _migration("20261016_1500_template_catalogue_provenance.py").upgrade()


def downgrade() -> None:
    """Nothing to undo: the data was only made more precise."""
