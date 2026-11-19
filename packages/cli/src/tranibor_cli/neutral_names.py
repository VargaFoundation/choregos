# SPDX-License-Identifier: Apache-2.0
"""The neutral names of ADR 0181 C1, read first, with the former names the API also serves until 0.30.0.

When the former names leave (`tranibor_contracts.window`), each fallback here leaves with them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The relation of the link to the change a work item proposes for review.
CHANGE_REQUEST = "change_request"
#: The former names of the renamed roles, and the role each names.
_FORMER_ROLES = {"developer": "contributor", "release_captain": "approver"}


def change_request_of(item: Mapping[str, Any]) -> str | None:
    """The URL of the change a work item proposes: its `change_request` link, else its former `pr_url`."""
    for link in item.get("links") or ():
        if isinstance(link, Mapping) and link.get("rel") == CHANGE_REQUEST and link.get("url"):
            return str(link["url"])
    found = item.get("pr_url")
    return str(found) if found else None


def neutral_role(role: str) -> str:
    """A role as the API answered it, under its neutral name."""
    return _FORMER_ROLES.get(role, role)
