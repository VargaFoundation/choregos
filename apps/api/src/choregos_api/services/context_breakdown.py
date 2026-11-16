# SPDX-License-Identifier: Apache-2.0
"""What fills a run's context (ADR 0141 §3-6): the breakdowns a run's events keep, and the gateway's.

A breakdown is read as the contract says it, numbers only (`breakdown_of`). The `prompt` and `hosted`
backends are measured where their requests are built; an ACP CLI's calls are measured by the gateway's
callback (S64-08b), which reads what the runner registered (`registered_parts`, the run's
`context.parts` event: digests and sizes, never the text) and posts each kept breakdown back
(`record_gateway_breakdown`).

The gateway writes while the runner does, and the runner numbers its own events from 1: a gateway
breakdown takes a `seq` above every runner's (`GATEWAY_SEQ_BASE`), the next one after the gateway's
last, so neither ever takes the other's place.
"""

from __future__ import annotations

from typing import Any

from choregos_core.context_breakdown import EVENT, PARTS_EVENT, SECTIONS
from choregos_core.domain import utcnow
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Run, RunEvent
from ..schemas import ContextBreakdownDto

#: The backends whose model calls the platform measures where it builds them (ADR 0141 §4): its own
#: loop and the hosted container's loopback model. An ACP CLI is measured by the gateway's callback,
#: and is measurable once a breakdown exists; `code` and `remote` cannot be seen into.
MEASURED_BACKENDS = frozenset({"prompt", "hosted"})
#: Above every `seq` a runner gives its events (it numbers them from 1, one per ACP message).
GATEWAY_SEQ_BASE = 1_000_000_000
#: The most registered paragraphs the gateway is served for one run.
MAX_PARTS = 4096


def breakdown_of(payload: Any) -> ContextBreakdownDto | None:
    """A kept event as the contract says it, numbers only: an event that is not one is left out, and a
    section the contract does not name is never served."""
    if not isinstance(payload, dict):
        return None
    sections = payload.get("sections")
    sections = sections if isinstance(sections, dict) else {}
    try:
        return ContextBreakdownDto.model_validate(
            {
                **{key: payload[key] for key in ("call", "at", "model", "prompt_tokens") if key in payload},
                "max_input_tokens": payload.get("max_input_tokens"),
                "source": payload.get("source"),
                "sections": {name: int(sections.get(name) or 0) for name in SECTIONS},
            }
        )
    except (ValidationError, TypeError, ValueError):
        return None


async def registered_parts(session: AsyncSession, run_id: str) -> dict[str, Any]:
    """What the run's runner registered, as the gateway's callback reads it: `{section, sha256, size}`
    per paragraph and the run's MCP servers. Nothing registered (yet): both lists empty."""
    payload = (
        await session.execute(
            select(RunEvent.payload)
            .where(RunEvent.run_id == run_id, RunEvent.type == PARTS_EVENT)
            .order_by(RunEvent.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    payload = payload if isinstance(payload, dict) else {}
    parts = [
        {"section": part["section"], "sha256": part["sha256"], "size": int(part.get("size") or 0)}
        for part in payload.get("parts") or []
        if isinstance(part, dict)
        and part.get("section") in SECTIONS
        and isinstance(part.get("sha256"), str)
        and isinstance(part.get("size", 0), int)
    ][:MAX_PARTS]
    servers = [server for server in payload.get("mcp_servers") or [] if isinstance(server, str)]
    return {"parts": parts, "mcp_servers": servers}


async def record_gateway_breakdown(
    session: AsyncSession, run: Run, payload: Any
) -> ContextBreakdownDto | None:
    """One breakdown the gateway kept, written as the run's `context.breakdown` event: its numbers
    only, `source: gateway` whatever it said. `None` for a payload that is not a breakdown."""
    breakdown = breakdown_of(payload)
    if breakdown is None:
        return None
    breakdown = breakdown.model_copy(update={"source": "gateway"})
    last = (
        await session.execute(
            select(func.max(RunEvent.seq)).where(RunEvent.run_id == run.id, RunEvent.seq > GATEWAY_SEQ_BASE)
        )
    ).scalar()
    session.add(
        RunEvent(
            run_id=run.id,
            seq=int(last or GATEWAY_SEQ_BASE) + 1,
            type=EVENT,
            payload=breakdown.model_dump(mode="json"),
            ts=utcnow(),
        )
    )
    await session.flush()
    return breakdown
