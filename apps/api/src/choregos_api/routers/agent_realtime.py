# SPDX-License-Identifier: Apache-2.0
"""A person speaking to a voice agent (S41-10, ADR 0080 §10): `GET …/agents/{slug}/realtime`, a
WebSocket speaking the OpenAI Realtime events, relayed to the gateway's realtime socket.

The door is the one of `POST …/responses`: the agent's `SEE` and `CONVERSE` in the project, the
session cookie or an API token. A browser sends the cookie to any site that opens a socket, so an
`Origin` that is not the console's or the API's is refused before anything is read.

Everything is decided before the socket is accepted — rights, the gateway's `realtime` capability,
the session and its key, the upstream socket: a refusal closes it with 1008 and its reason, and no
key outlives it.

The same door serves a hosted agent that declares `invocations_ws` (S41-21): its frames are relayed
to the container's own socket in the session's pod (`agent_session_sockets`).

A caller whose socket dropped comes back with `session` (the session's id, in the socket's first
event, `choregos.session`) and `after` (the `seq` of the last event it read): its rights are checked
again, the session must be its own, and the events it missed are sent first. A voice session is held
by the API process that opened it, which another one answers with 1013; a hosted one is read from
the journal by any of them.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Query, WebSocket

from ..config import get_settings
from ..db.models import AgentSessionRecord
from ..db.session import session_scope
from ..deps import current_principal
from ..errors import ApiError, not_found
from ..logging import get_logger
from ..origin_guard import origin_allowed
from ..schemas.agent_sessions import SLUG
from ..secret_store import get_secret_store
from ..services.agent_governance import Action, decide
from ..services.agent_realtime import (
    VoiceRelay,
    VoiceSession,
    end_voice_session,
    held_relay,
    realtime_connector,
    realtime_url,
    require_realtime_gateway,
)
from ..services.agent_session_sockets import serve_socket
from ..services.agent_sessions import budget_refusal, open_session, opened_terms, secret_name, session_tools
from ..services.realtime_spend import RealtimePrices
from .agents import _charger, _exiger, _projet

router = APIRouter(tags=["agents"])
log = get_logger(__name__)

#: A minute of `max_session_minutes`, in seconds (a test shortens it).
SECONDS_PER_MINUTE = 60.0
#: A WebSocket close reason is at most 123 bytes.
_REASON = 120


@router.websocket("/orgs/{org}/agents/{slug}/realtime")
async def agent_realtime(
    websocket: WebSocket,
    org: str,
    slug: str,
    project: str = Query(pattern=SLUG),
    max_usd: float | None = Query(default=None, gt=0, le=100),
    session: str | None = Query(default=None, max_length=64),
    after: int = Query(default=0, ge=0),
) -> None:
    settings = get_settings()
    if not origin_allowed(websocket.headers.get("origin"), settings):
        await websocket.close(code=1008, reason="this origin may not open a voice session")
        return
    if session is not None:
        await _resume(websocket, org, slug, project, session, after)
        return
    try:
        async with session_scope() as db:
            authorization = websocket.headers.get("authorization")
            principal = await current_principal(websocket, db, settings, authorization)
            organisation, agent, subject = await _charger(db, principal, org, slug)
            target = await _projet(db, organisation, project)
            _exiger(decide(principal, org, subject, Action.CONVERSE, project_slug=target.slug))
            from ..services.agents import resoudre_l_agent

            if await _served_kind(db, organisation.id, target.id, agent.slug) == "hosted":
                record = await open_session(
                    db, principal, organisation, agent.slug, target, max_usd=max_usd, runtime="socket"
                )
                hosted = (record.id, organisation.slug)
            else:
                hosted = None
                await require_realtime_gateway(db, organisation.id)
                record = await open_session(
                    db, principal, organisation, agent.slug, target, max_usd=max_usd, runtime="voice"
                )
                served = f"{record.agent_slug}@{record.agent_version}"
                spec = (await resoudre_l_agent(db, organisation.id, target.id, served)).spec
                name = secret_name(record.id)
                key = (await get_secret_store().get_many(db, organisation.id, [name])).get(name, "")
                terms = await opened_terms(db, record)
                voice = VoiceSession(
                    session_id=record.id,
                    org_slug=organisation.slug,
                    org_id=organisation.id,
                    project_id=target.id,
                    model=record.model,
                    key=key,
                    spec=spec,
                    tools=await session_tools(db, spec, target),
                    max_seconds=getattr(spec.runtime, "max_session_minutes", 1) * SECONDS_PER_MINUTE,
                    prices=RealtimePrices.from_payload(terms["prices"]),
                    reserved_usd=float(terms["reserved_usd"]),
                    usd_per_minute=terms.get("max_usd_per_minute"),
                    minute_s=SECONDS_PER_MINUTE,
                )
    except ApiError as refusal:
        await websocket.close(code=1008, reason=(refusal.detail or refusal.title)[:_REASON])
        return
    if hosted is not None:
        await serve_socket(websocket, *hosted, after=0, first=True)
        return

    try:
        upstream = await realtime_connector().connect(
            realtime_url(settings.gateway_url, voice.model), {"Authorization": f"Bearer {voice.key}"}
        )
    except Exception as refused:
        reason = "budget_exceeded" if budget_refusal(str(refused)) else "upstream_refused"
        log.warning("agent_realtime.upstream_refused", session_id=voice.session_id, error=str(refused)[:300])
        await end_voice_session(voice, reason)
        await websocket.close(code=1011, reason=f"the gateway refused the realtime session: {reason}")
        return

    relay = VoiceRelay(upstream, voice)
    try:
        await relay.start()
    except Exception:
        with contextlib.suppress(Exception):
            await upstream.close()
        await end_voice_session(voice, "upstream_closed")
        await websocket.close(code=1011, reason="the model's socket closed")
        return
    await websocket.accept()
    await _serve(websocket, relay, 0)


async def _resume(
    websocket: WebSocket, org: str, slug: str, project: str, session_id: str, after: int
) -> None:
    """A caller coming back to its session after its socket dropped (S41-21): the same door, the
    same rights checked again, and the session must be its own, of this agent, in this project."""
    settings = get_settings()
    try:
        async with session_scope() as db:
            authorization = websocket.headers.get("authorization")
            principal = await current_principal(websocket, db, settings, authorization)
            organisation, agent, subject = await _charger(db, principal, org, slug)
            target = await _projet(db, organisation, project)
            _exiger(decide(principal, org, subject, Action.CONVERSE, project_slug=target.slug))
            record = await db.get(AgentSessionRecord, session_id)
            if (
                record is None
                or record.org_id != organisation.id
                or record.user_id != principal.user_id
                or record.agent_slug != agent.slug
                or record.project_id != target.id
            ):
                raise not_found("Agent session", session_id)
            if record.status != "open":
                raise ApiError(410, "Session ended", f"the session ended: {record.reason}")
            kind = await _served_kind(
                db, organisation.id, target.id, f"{record.agent_slug}@{record.agent_version}"
            )
            org_slug = organisation.slug
    except ApiError as refusal:
        await websocket.close(code=1008, reason=(refusal.detail or refusal.title)[:_REASON])
        return
    if kind == "hosted":
        await serve_socket(websocket, session_id, org_slug, after=after, first=False)
        return
    relay = held_relay(session_id)
    if relay is None:
        # The relay lives in the API process that opened the session: another one cannot resume it.
        await websocket.close(code=1013, reason="this session is not held here: try again")
        return
    refused = relay.refusal(after)
    if refused is not None:
        await websocket.close(code=refused.code, reason=refused.message[:_REASON])
        return
    await websocket.accept()
    await _serve(websocket, relay, after)


async def _serve(websocket: WebSocket, relay: VoiceRelay, after: int) -> None:
    ending = await relay.attach(websocket, after)
    if ending is not None:
        with contextlib.suppress(Exception):
            await websocket.close(code=ending.code, reason=ending.message[:_REASON])


async def _served_kind(db: Any, org_id: str, project_id: str, agent: str) -> str | None:
    from ..services.agents import AgentIndisponible, resoudre_l_agent

    try:
        spec = (await resoudre_l_agent(db, org_id, project_id, agent)).spec
    except AgentIndisponible:
        return None  # `open_session` says why
    return spec.runtime.kind if spec.runtime is not None else None
