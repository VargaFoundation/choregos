# SPDX-License-Identifier: Apache-2.0
"""A decision or a confirmation is the person's own gesture (ADR 0154 §7, G10).

Deciding a human step, approving or rejecting a governed action, approving a release, confirming or refusing
a write an assistant proposed: each is accepted from a principal of kind `user` whose identity was proved by a
session of the person, which is a console cookie, an embedded frame's token or a developer surface's session
(ADR 0154 §6), and refused (`decision_requires_session`) for an API token (an MCP or A2A token, a machine
account's token included), a run token and an outpost. The MCP door still decides nothing (ADR 0030 §5).

Two routes accepted a token before this rule: `postDecision` and the conversation actions; four more, which
widen what a run or the platform may do (egress, plugin installs, skill versions, assistant knowledge
updates), joined them by the amendment of 2026-11-19 (#1926). A deployment may keep each accepting a token
for one minor version (`CHOREGOS_LEGACY_DECISION_ROUTES`, see `docs/deployment.md`); the other gestures have
no such setting.
"""

from __future__ import annotations

from typing import Final

from .config import Settings
from .errors import ApiError
from .rbac import Principal

#: What proved the identity, for a gesture that is the person's: see `Principal.credential`.
PERSON_CREDENTIALS: Final = frozenset({"cookie", "frame", "surface"})
CODE: Final = "decision_requires_session"
#: The routes a deployment may reopen for one minor version, by the name the setting gives them.
POST_DECISION: Final = "post_decision"
CONVERSATION_ACTIONS: Final = "conversation_actions"
EGRESS_REQUESTS: Final = "egress_requests"
PLUGIN_INSTALLS: Final = "plugin_installs"
SKILL_VERSIONS: Final = "skill_versions"
KNOWLEDGE_UPDATES: Final = "assistant_knowledge_updates"
LEGACY_ROUTES: Final = frozenset(
    {POST_DECISION, CONVERSATION_ACTIONS, EGRESS_REQUESTS, PLUGIN_INSTALLS, SKILL_VERSIONS, KNOWLEDGE_UPDATES}
)


def holds_a_person_session(principal: Principal) -> bool:
    return principal.kind == "user" and principal.credential in PERSON_CREDENTIALS


def refusal(gesture: str) -> ApiError:
    return ApiError(
        403,
        "Forbidden",
        f"{gesture} is the person's own gesture: it needs their session (the console, or `choregos login`), "
        f"not an API token (decision_requires_session)",
        extra={"code": CODE},
    )


def require_person_session(
    principal: Principal, gesture: str, *, settings: Settings | None = None, legacy: str | None = None
) -> None:
    """Refuse a principal that is not the person in a session. `legacy` names the route's entry in
    `TRANIBOR_LEGACY_DECISION_ROUTES`: when the deployment lists it, a token still passes there."""
    if holds_a_person_session(principal):
        return
    if legacy is not None and settings is not None and legacy in settings.legacy_decision_route_set():
        return
    raise refusal(gesture)
