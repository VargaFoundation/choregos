# SPDX-License-Identifier: Apache-2.0
"""The "internal" tracker: there is no other, the platform holds the ticket.

Choregos' default model is to be **a guest** of an existing tracker: GitHub Issues, Jira. The
state, the cost and the proofs are reflected there because that is where humans look. Two
situations do not fit this mould:

- a trade whose requests exist nowhere else (a staffing need, a case to examine) and for whom
  Choregos' board IS the place where people look;
- a demonstration bench, which has neither a GitHub organisation nor a Jira site.

This connector then returns plain no-ops rather than errors: nothing to reflect, nothing to
comment outside. What is lost is the round trip with a third-party tool: nobody is notified
elsewhere, and a ticket moved by hand in another tool does not come back up. The rest of the
platform does not change one iota.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from choregos_contracts import InboundEvent, ProjectConfig
from choregos_core.domain import NewItem, TrackerStateMapping, WorkItemData


class InternalTracker:
    """Choregos' database is the source; this connector talks to nobody.

    `owns_items = False` is the important fact, and it has a history. Delivered on 2026-09-23
    as a series of no-ops, this connector left two holes that the bench showed the next day:

    - `create_item` returned the TITLE as the ticket key. The findings promoted to tickets
      therefore carried keys such as `[docs] Missing candidate profiles base`.
    - `list_candidates` returning `[]`, reconciliation never discovered those tickets: once
      created, they stayed in `inbox` **forever**.

    Both are cured in the same place, and not here: when the tracker does not own the tickets,
    it is the platform that gives them a key and discovers them in its own database. This
    connector SAYS so, and the orchestrator takes it into account.
    """

    owns_items = False

    async def fetch_item(self, key: str) -> WorkItemData:
        # The ticket lives in Choregos' database: the orchestrator already has it in hand, and
        # what would be returned here would be a poorer copy. So the strict minimum is returned.
        return WorkItemData(key=key, title=key)

    async def create_item(self, data: NewItem) -> str:
        # The key is given by the platform (`owns_items = False`): returning the title, as was
        # done, produced unreadable and non-unique ticket keys.
        return ""

    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None:
        return None

    async def upsert_status_comment(self, key: str, markdown: str, marker: str) -> None:
        return None

    async def close_item(self, key: str) -> None:
        # The work item lives in the database: `closed_at` already closes it.
        return None

    async def comment(self, key: str, markdown: str) -> str:
        return ""

    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None:
        return None

    async def set_fields(self, key: str, fields: dict[str, Any]) -> None:
        return None

    async def list_candidates(self, project: ProjectConfig) -> list[str]:
        return []

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        return []

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        # No webhook is expected: accepting one would open a door that leads nowhere.
        return False
