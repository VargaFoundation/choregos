# SPDX-License-Identifier: Apache-2.0
"""A memory's accepted facts as an Open Knowledge Format bundle (ADR 0174 point 7, S72-04).

One file per active fact: its kind is the concept's `type`, its subject the `title`, its content the
body. What the platform can prove goes in the header, nothing else:

- `verified: [{by: human:<user id>, at}]` only when a person accepted the fact through the API: the
  audit log's `memory.accept` row, by a user, on that fact, whose decision found it. A fact a run
  wrote, one the auto-accept rule took (no person decided), or one whose decision row is missing is
  exported without `verified`;
- `sources` from the fact's provenance (the run, the reference it came from);
- `valid_from`/`valid_to` kept as the fact's own keys. OKF has no bi-temporal validity: a fact whose
  `valid_to` is past is `status: deprecated`, which is all OKF can say of it.

The adapter must list one memory's facts and only those (`list_facts`). Ecphoria cannot: it does not
read the namespace header Choregos sends, so a listing would mix every memory of the tenant. Its
export is refused, never approximated.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import yaml
from choregos_core.domain import Memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditLog
from ..knowledge import okf

#: How many ids one `IN` of the decision lookup carries.
_CHUNK = 500


def _instant(moment: datetime | str | None) -> str | None:
    if moment is None:
        return None
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            return None
    moment = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


async def deciders(session: AsyncSession, org_id: str, ids: list[str]) -> dict[str, tuple[str, datetime]]:
    """`fact id → (user id, when)` of the person who accepted it, from the audit log.

    Only a user's decision counts (a token or the system is no person), and only one that found the
    fact: a decision row says `found: false` when the fact was not where it was decided. The last
    accepting decision wins."""
    found: dict[str, tuple[str, datetime]] = {}
    for start in range(0, len(ids), _CHUNK):
        rows = await session.execute(
            select(AuditLog)
            .where(
                AuditLog.org_id == org_id,
                AuditLog.action == "memory.accept",
                AuditLog.target_type == "memory",
                AuditLog.actor_kind == "user",
                AuditLog.target_id.in_(ids[start : start + _CHUNK]),
            )
            .order_by(AuditLog.ts)
        )
        for row in rows.scalars():
            if row.actor_id and row.target_id and (row.payload or {}).get("found", True) is not False:
                found[row.target_id] = (row.actor_id, row.ts)
    return found


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60].rstrip("-") or "fact"


def _sources(provenance: dict[str, Any]) -> list[dict[str, str]]:
    run_id = provenance.get("run_id")
    ref = provenance.get("ref")
    origin = str(provenance.get("source") or "")
    sources: list[dict[str, str]] = []
    if ref:
        sources.append({"resource": str(ref), **({"title": origin} if origin else {})})
    if run_id:
        sources.append({"resource": f"run:{run_id}", "title": "the run that proposed it"})
    return sources


def _title(memory: Memory) -> str:
    """The subject; without one, the content's first line; without either, the id."""
    if memory.subject.strip():
        return memory.subject.strip()
    lines = memory.content.strip().splitlines()
    return lines[0][:80] if lines else memory.id


def fact_markdown(memory: Memory, decided: tuple[str, datetime] | None, now: datetime) -> str:
    """One fact as an OKF concept. Deterministic for a given fact and decision."""
    header: dict[str, Any] = {
        "type": str(memory.kind) or okf.DEFAULT_TYPE,
        "title": _title(memory),
    }
    sources = _sources(memory.provenance or {})
    if sources:
        header["sources"] = sources
    if decided is not None:
        header["verified"] = [{"by": f"human:{decided[0]}", "at": _instant(decided[1])}]
    valid_from, valid_to = _instant(memory.valid_from), _instant(memory.valid_to)
    if valid_to is not None and memory.valid_to is not None and _past(memory.valid_to, now):
        header["status"] = "deprecated"
    if valid_from:
        header["valid_from"] = valid_from
    if valid_to:
        header["valid_to"] = valid_to
    header["memory_fact"] = memory.id
    text = yaml.safe_dump(header, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)
    return f"---\n{text}---\n\n{memory.content.rstrip()}\n"


def _past(moment: datetime, now: datetime) -> bool:
    moment = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return now >= moment


def files_of(
    facts: list[Memory], decided: dict[str, tuple[str, datetime]], now: datetime
) -> list[dict[str, str]]:
    """The bundle's files, in a stable order (subject, then id)."""
    ordered = sorted(facts, key=lambda m: (m.subject, m.id))
    return [
        {
            "path": f"{_slug(_title(m))}-{_slug(m.id)}.md",
            "title": _title(m),
            "filename": f"fact {m.id}",
            "markdown": fact_markdown(m, decided.get(m.id), now),
        }
        for m in ordered
    ]


__all__ = ["deciders", "fact_markdown", "files_of"]
