# SPDX-License-Identifier: Apache-2.0
"""A person speaking to a voice agent (S41-10, ADR 0080 §10): `GET …/agents/{slug}/realtime`, a
WebSocket speaking the OpenAI Realtime events, relayed to the gateway's realtime socket.

The door is the one of `POST …/responses`: the agent's `SEE` and `CONVERSE` in the project, the
session cookie or an API token. A browser sends the cookie to any site that opens a socket, so an
`Origin` that is not the console's or the API's is refused before anything is read.

Everything is decided before the socket is accepted — rights, the gateway's `realtime` capability,
the session and its key, the upstream socket: a refusal closes it with 1008 and its reason, and no
key outlives it.
"""

from __future__ import annotations

import contextlib
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, WebSocket

from ..config import Settings, get_settings
from ..db.session import session_scope
from ..deps import current_principal
from ..errors import ApiError
from ..logging import get_logger
from ..schemas.agent_sessions import SLUG
from ..secret_store import get_secret_store
from ..services.agent_governance import Action, decide
from ..services.agent_realtime import (
    Ending,
    VoiceSession,
    end_voice_session,
    realtime_connector,
    realtime_url,
    relay,
    require_realtime_gateway,
)
from ..services.agent_sessions import budget_refusal, open_session, secret_name, session_tools
from .agents import _charger, _exiger, _projet

router = APIRouter(tags=["agents"])
log = get_logger(__name__)

#: A minute of `max_session_minutes`, in seconds (a test shortens it).
SECONDS_PER_MINUTE = 60.0
#: A WebSocket close reason is at most 123 bytes.
_REASON = 120


def _origin(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}"


def origin_allowed(origin: str | None, settings: Settings) -> bool:
    """No `Origin` (a CLI, a server) passes; a browser's must be the console's, the API's, or CORS's."""
    if not origin:
        return True
    allowed = {_origin(settings.public_url), _origin(settings.api_url), *map(_origin, settings.cors_list)}
    return _origin(origin) in allowed


@router.websocket("/orgs/{org}/agents/{slug}/realtime")
async def agent_realtime(
    websocket: WebSocket,
    org: str,
    slug: str,
    project: str = Query(pattern=SLUG),
    max_usd: float | None = Query(default=None, gt=0, le=100),
) -> None:
    settings = get_settings()
    if not origin_allowed(websocket.headers.get("origin"), settings):
        await websocket.close(code=1008, reason="this origin may not open a voice session")
        return
    try:
        async with session_scope() as db:
            authorization = websocket.headers.get("authorization")
            principal = await current_principal(websocket, db, settings, authorization)
            organisation, agent, subject = await _charger(db, principal, org, slug)
            target = await _projet(db, organisation, project)
            _exiger(decide(principal, org, subject, Action.CONVERSE, project_slug=target.slug))
            await require_realtime_gateway(db, organisation.id)
            record = await open_session(
                db, principal, organisation, agent.slug, target, max_usd=max_usd, runtime="voice"
            )
            from ..services.agents import resoudre_l_agent

            served = f"{record.agent_slug}@{record.agent_version}"
            spec = (await resoudre_l_agent(db, organisation.id, target.id, served)).spec
            name = secret_name(record.id)
            key = (await get_secret_store().get_many(db, organisation.id, [name])).get(name, "")
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
            )
    except ApiError as refusal:
        await websocket.close(code=1008, reason=(refusal.detail or refusal.title)[:_REASON])
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

    await websocket.accept()
    ending = Ending("relay_error", 1011, "the relay failed")
    try:
        ending = await relay(websocket, upstream, voice)
    finally:
        with contextlib.suppress(Exception):
            await upstream.close()
        await end_voice_session(voice, ending.reason)
    with contextlib.suppress(Exception):
        await websocket.close(code=ending.code, reason=ending.message[:_REASON])
