# SPDX-License-Identifier: Apache-2.0
"""Remote agents over A2A (ADR 0062): what publication reads, checks and pins.

A remote agent is only as trustworthy as the card somebody read. Publication fetches the card (https
only, no redirect to another host, bounded), checks that the skill exists and that the credential the
version names is of a type the card accepts (an API key, in a header the card names), and pins the
card: its URL, protocol version, skills and SHA-256. The runner reads the card again before it sends
anything and refuses on drift.
"""

from __future__ import annotations

from typing import Any

import httpx
from choregos_adapters.a2a import (
    AUTH_API_KEY,
    AUTH_BEARER,
    AUTH_NONE,
    AUTH_OAUTH,
    AgentCard,
    CardError,
    fetch_card,
)
from choregos_core.egress import EgressRefusedError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import RemoteCredential
from ..errors import unprocessable
from ..schemas import A2aPinnedCard, A2aRuntime, AgentSpec

#: Tests (and `CHOREGOS_FAKES` demos) route every outbound A2A/OAuth call through one transport.
_TRANSPORT: list[httpx.AsyncBaseTransport] = []


def set_a2a_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """For tests and the fakes: the transport the card fetch, DCR and token calls go through."""
    _TRANSPORT.clear()
    if transport is not None:
        _TRANSPORT.append(transport)


def a2a_transport() -> httpx.AsyncBaseTransport:
    """Every outbound A2A/OAuth call goes through the organisation's egress guard (S39-22)."""
    from choregos_adapters.http.egress import guarded

    return guarded(_TRANSPORT[0] if _TRANSPORT else None)


async def read_card(url: str) -> AgentCard:
    """The card at `url`, or 422 with why."""
    try:
        return await fetch_card(url, transport=a2a_transport())
    except EgressRefusedError as exc:
        raise unprocessable(str(exc)) from exc
    except CardError as exc:
        raise unprocessable(f"the agent card cannot be used: {exc}") from exc
    except httpx.HTTPError as exc:
        raise unprocessable(f"the agent card is unreachable ({type(exc).__name__})") from exc


async def credential_named(session: AsyncSession, org_id: str, name: str) -> RemoteCredential | None:
    found: RemoteCredential | None = (
        await session.execute(
            select(RemoteCredential).where(RemoteCredential.org_id == org_id, RemoteCredential.name == name)
        )
    ).scalar_one_or_none()
    return found


def credential_fits(card: AgentCard, credential: RemoteCredential) -> bool:
    """The card accepts this credential: its type, and for an API key the header it travels in."""
    if credential.type == AUTH_API_KEY:
        header = str((credential.config or {}).get("header") or "")
        return AUTH_API_KEY in card.auth_types and card.takes_api_key_in(header)
    return credential.type in {AUTH_OAUTH, AUTH_BEARER, AUTH_NONE} and credential.type in card.auth_types


async def pin_remote_runtime(session: AsyncSession, org_id: str, spec: AgentSpec) -> AgentSpec:
    """The spec with its `pinned_card` filled, after the checks of ADR 0062 §2. Not an `a2a` spec: as is."""
    runtime = spec.runtime
    if not isinstance(runtime, A2aRuntime):
        return spec
    from .egress import egress_scope_for_org

    async with egress_scope_for_org(session, org_id, "api", f"remote-agent:{runtime.card_url}"):
        card = await read_card(runtime.card_url)
    problems: list[dict[str, Any]] = []
    if runtime.skill is not None and card.skill(runtime.skill) is None:
        known = ", ".join(s.id for s in card.skills) or "none"
        problems.append(
            {"loc": ["runtime", "skill"], "msg": f"the card has no skill `{runtime.skill}` (it has: {known})"}
        )
    problems += await _credential_problems(session, org_id, runtime, card)
    if problems:
        raise unprocessable("the remote agent cannot be published", problems)
    pinned = A2aPinnedCard(
        url=card.url,
        protocol_version=card.protocol_version,
        digest=card.digest,
        skills=[s.id for s in card.skills],
    )
    return spec.model_copy(update={"runtime": runtime.model_copy(update={"pinned_card": pinned})})


async def _credential_problems(
    session: AsyncSession, org_id: str, runtime: A2aRuntime, card: AgentCard
) -> list[dict[str, Any]]:
    loc = ["runtime", "credential"]
    if runtime.credential is None:
        if AUTH_NONE in card.auth_types:
            return []
        accepted = ", ".join(card.auth_types) or "an unsupported scheme"
        return [{"loc": loc, "msg": f"the card requires authentication ({accepted}): name a credential"}]
    credential = await credential_named(session, org_id, runtime.credential)
    if credential is None:
        return [{"loc": loc, "msg": f"no remote credential `{runtime.credential}` in this organisation"}]
    if credential.status == "revoked":
        return [{"loc": loc, "msg": f"the remote credential `{runtime.credential}` is revoked"}]
    if credential_fits(card, credential):
        return []
    header = (credential.config or {}).get("header")
    if credential.type == AUTH_API_KEY and AUTH_API_KEY in card.auth_types:
        return [
            {
                "loc": loc,
                "msg": f"the credential `{credential.name}` sends its key in `{header}`, "
                f"the card asks for it in: {', '.join(card.api_key_headers)}",
            }
        ]
    return [
        {
            "loc": loc,
            "msg": f"the credential `{credential.name}` is `{credential.type}`, "
            f"the card accepts: {', '.join(card.auth_types) or 'nothing this platform supports'}",
        }
    ]
