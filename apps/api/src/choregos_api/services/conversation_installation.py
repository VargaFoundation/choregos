# SPDX-License-Identifier: Apache-2.0
"""What an assistant's installation says about its conversations, read again at every turn (ADR 0134
points 7 and 8, ADR 0133 point 5).

- The installation must still be `ready`: one that became `incomplete` (a binding removed, a plugin
  version that needs more) or `suspended` (its plugin removed) after the conversation opened refuses the
  turn, 409 with the status, before any key or model call. The conversation and its history stay.
- The limits (`max_turns`, `retention_days`, `idle_close_minutes`) are the installation's policy NOW (the
  manifest's `policy.conversation`, overridden by `policy_overrides.conversation`): a change of policy
  reaches the conversations already open at their next turn, and `retention_until` is set from it.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, AssistantInstallation
from ..errors import ApiError
from . import assistants
from .assistant_access import conversation_policy


async def apply_installation(session: AsyncSession, record: AgentSessionRecord) -> None:
    """Refuses the turn of a conversation whose installation is not ready, and takes the installation's
    current limits. A conversation of no installation is left as it is."""
    if record.installation_id is None:
        return
    row = await session.get(AssistantInstallation, record.installation_id)
    if row is None:
        return
    status, _items = await assistants.refresh(session, row)
    if status != assistants.READY:
        raise ApiError(
            409,
            "Assistant unavailable",
            f"`{row.slug}` is {status}: the conversation stays, and goes on once the assistant is ready",
        )
    policy = conversation_policy(await assistants.manifest(session, row), row)
    record.max_turns = policy.get("max_turns", record.max_turns)
    record.retention_days = policy.get("retention_days", record.retention_days)
    record.idle_close_minutes = policy.get("idle_close_minutes", record.idle_close_minutes)
    await session.flush()  # the claim reloads the row under its lock: what is changed here must be written
