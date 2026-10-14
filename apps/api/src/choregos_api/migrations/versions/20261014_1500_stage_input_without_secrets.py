# SPDX-License-Identifier: Apache-2.0
"""`runs.stage_input` keeps no secret (#519): the run token and the spent gateway keys are scrubbed

Data only, no schema change. Every row loses `callbacks.run_token` (replaced by `[REDACTED]`: the
contract requires a string there, and nothing reads it back — `start_run` mints the token for the
run's Secret). A row loses `gateway_key` (set to null) once its run is over: spend collected, a
final status, its key revoked, expired or unknown. A live run keeps its key: its runner may still
read it from `GET /internal/runs/{id}/input`, and `collect_spend` drops it when the run ends.

Idempotent: a scrubbed row is left as it is. Under PostgreSQL the tables are under forced RLS and
the migration job runs as the application role: without `set_config('app.current_orgs', '*', true)`
every UPDATE would touch zero rows, silently.

No downgrade: a scrubbed secret cannot be restored, and nothing needs it back — the tokens expired
with their run and the keys are revoked or past their TTL. `downgrade()` is a no-op on purpose.

Revision ID: e7a9c1b3d5f7
Revises: b9d1f3a5c7e8
Create Date: 2026-10-14 15:00:00+00:00
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "e7a9c1b3d5f7"
down_revision: str | None = "b9d1f3a5c7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

#: Frozen here rather than imported: a migration must not change when the code does.
REDACTED = "[REDACTED]"
FINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})

RUNS = sa.table(
    "runs",
    sa.column("id", sa.String),
    sa.column("status", sa.String),
    sa.column("spend_collected", sa.Boolean),
    sa.column("gateway_key_id", sa.String),
    sa.column("stage_input", Json()),
)
GATEWAY_KEYS = sa.table(
    "gateway_keys",
    sa.column("key_id", sa.String),
    sa.column("revoked", sa.Boolean),
    sa.column("expires_at", sa.DateTime(timezone=True)),
)


def _key_is_dead(row: Any, now: datetime) -> bool:
    if row["key_id"] is None:  # no key row: nothing says it is alive
        return True
    if row["revoked"]:
        return True
    expires_at = row["expires_at"]
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:  # SQLite hands back a naive UTC datetime
        expires_at = expires_at.replace(tzinfo=UTC)
    return bool(expires_at <= now)


def scrubbed(row: Any, now: datetime) -> dict[str, Any] | None:
    """The row's `stage_input` without its secrets, or None when it holds none."""
    document = row["stage_input"]
    if not isinstance(document, dict):
        return None
    cleaned = dict(document)
    callbacks = cleaned.get("callbacks")
    if isinstance(callbacks, dict) and callbacks.get("run_token") not in (None, REDACTED):
        cleaned["callbacks"] = {**callbacks, "run_token": REDACTED}
    over = bool(row["spend_collected"]) or row["status"] in FINAL_STATUSES or _key_is_dead(row, now)
    if cleaned.get("gateway_key") is not None and over:
        cleaned["gateway_key"] = None
    return cleaned if cleaned != document else None


def scrub_rows(connection: Any, now: datetime | None = None) -> list[str]:
    """Scrubs every row that still holds a secret; returns the ids of the rows changed."""
    now = now or datetime.now(UTC)
    query = sa.select(
        RUNS.c.id,
        RUNS.c.status,
        RUNS.c.spend_collected,
        RUNS.c.stage_input,
        GATEWAY_KEYS.c.key_id,
        GATEWAY_KEYS.c.revoked,
        GATEWAY_KEYS.c.expires_at,
    ).select_from(RUNS.outerjoin(GATEWAY_KEYS, GATEWAY_KEYS.c.key_id == RUNS.c.gateway_key_id))
    changed: list[str] = []
    for row in connection.execute(query.where(RUNS.c.stage_input.is_not(None))).mappings().all():
        cleaned = scrubbed(row, now)
        if cleaned is None:
            continue
        connection.execute(RUNS.update().where(RUNS.c.id == row["id"]).values(stage_input=cleaned))
        changed.append(row["id"])
    log.info("runs.stage_input: %d row(s) scrubbed of their secrets", len(changed))
    return changed


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    scrub_rows(connection)


def downgrade() -> None:
    """Nothing to undo: a scrubbed secret cannot be restored, and nothing needs it back."""
