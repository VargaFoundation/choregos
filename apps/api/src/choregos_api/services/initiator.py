# SPDX-License-Identifier: Apache-2.0
"""Who started a work item, as `<kind>:<subject>` (ADR 0077 §2, S40-22).

Written once at birth by the door the work item came through, copied onto each run at stage
start, shown at the head of the run's chain (`GET /runs/{id}/access`). Never sent by an agent:
every value here is built from what the API or the orchestrator already holds.

The kinds: `person:<email>` (console, REST, API token), `mcp:<email>` (the MCP door),
`a2a:<client>` (the inbound A2A door, S40-23), `schedule:<name>`, `work_item:<key>` (a fan-out
child), `tracker:<kind>` (a tracker event or sync), `finding:<id>`, `eval`, `demo`,
`trial:<email>`, and `system` for a system principal on a door that names nothing better.
"""

from __future__ import annotations

from ..rbac import Principal

#: The kinds an initiator may have; the access record shows the value as is.
KINDS = frozenset(
    {"person", "mcp", "a2a", "schedule", "work_item", "tracker", "finding", "eval", "demo", "trial", "system"}
)


def initiator(kind: str, subject: str | None = None) -> str:
    """`<kind>:<subject>`, or the kind alone when there is no subject. An unknown kind is a bug."""
    if kind not in KINDS:
        raise ValueError(f"unknown initiator kind `{kind}`")
    subject = (subject or "").strip()
    return f"{kind}:{subject}"[:255] if subject else kind


def of_principal(principal: Principal, channel: str) -> str:
    """The initiator of a work item a principal creates through a door (`creer_un_ticket`)."""
    if principal.kind != "user":
        return initiator("system")
    return initiator("mcp" if channel == "mcp" else "person", principal.email)
