# SPDX-License-Identifier: Apache-2.0
"""A person speaking to a voice agent (S41-10, ADR 0080 §10): the relay between the caller's
WebSocket and the gateway's realtime one.

The session is the one of `agent_sessions`: the same reservation, ONE gateway key minted for it and
sealed in the secret store, the same journal and the same close. What changes is the conversation:
audio events relayed both ways, in the OpenAI Realtime shape.

The agent owns the session. The relay configures it once (`session.update`: the agent's
instructions, voice, audio, turn detection, the read tools its grant offers in the project) and
refuses what would change it: a client's `session.update`, a `response.create` carrying its own
instructions or tools, a `conversation.item.create` that is not the caller's own message (a
`function_call_output` would forge a tool's answer). Each is answered with an `error` event and
dropped: it never reaches the model.

A function call of the model runs through the courtier like a prompt session's (`call_session_tool`),
only if the grant offers the tool; its output goes back to the model, never to the caller first.

What is kept is text: the caller's transcript, the agent's transcript and text, tool calls and
refusals. Audio is relayed and forgotten. A frame over `MAX_FRAME_BYTES` closes the caller's socket
with 1009; `max_session_minutes` closes it with 1000; either way the session closes, its spend booked.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

from choregos_adapters.base import RealtimeClosed, RealtimeConnector, RealtimeLink
from choregos_adapters.registry import fakes_enabled

from ..config import get_settings
from ..db.models import AgentSessionRecord, Project
from ..db.session import session_scope
from ..errors import unprocessable
from ..logging import get_logger
from .agent_sessions import (
    budget_refusal,
    call_session_tool,
    close_session,
    journal,
    session_gateway,
    tool_parameters,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..schemas import AgentSpec
    from .courtier import OutilDuCourtier

log = get_logger(__name__)

#: The largest event a caller may send: about sixteen seconds of 24 kHz PCM16, base64-encoded.
MAX_FRAME_BYTES = 1024 * 1024

#: What a caller may send as it is: its audio, the end of its turn, an interruption, an edit of
#: its own items.
_RELAYED = frozenset(
    {
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "input_audio_buffer.clear",
        "response.cancel",
        "conversation.item.truncate",
        "conversation.item.delete",
    }
)
#: The content a caller's own message may carry.
_CALLER_CONTENT = frozenset({"input_text", "input_audio"})

#: Transcripts and text: what the journal keeps of a conversation (both the GA and beta names).
_SPOKEN = {
    "conversation.item.input_audio_transcription.completed": ("input.transcript", "transcript"),
    "response.output_audio_transcript.done": ("output.transcript", "transcript"),
    "response.audio_transcript.done": ("output.transcript", "transcript"),
    "response.output_text.done": ("output.text", "text"),
    "response.text.done": ("output.text", "text"),
}

_OVERRIDES: dict[str, Any] = {}


def set_realtime_connector(connector: RealtimeConnector | None) -> None:
    """For tests and the fakes: what opens the gateway's realtime socket (`None` forgets it)."""
    _OVERRIDES.pop("connector", None)
    if connector is not None:
        _OVERRIDES["connector"] = connector


def realtime_connector() -> RealtimeConnector:
    if "connector" in _OVERRIDES:
        found: RealtimeConnector = _OVERRIDES["connector"]
        return found
    if fakes_enabled():
        from choregos_adapters.fakes import FakeRealtimeUpstream

        return _OVERRIDES.setdefault("connector", FakeRealtimeUpstream())  # type: ignore[no-any-return]
    return WebsocketsConnector()


class WebsocketsConnector:
    """The gateway's realtime socket, through `websockets` (LiteLLM relays `/realtime`)."""

    async def connect(self, url: str, headers: dict[str, str]) -> RealtimeLink:
        from websockets.asyncio.client import connect

        socket = await connect(url, additional_headers=headers, max_size=8 * MAX_FRAME_BYTES)
        return _WebsocketsLink(socket)


class _WebsocketsLink:
    def __init__(self, socket: Any) -> None:
        self._socket = socket

    async def send(self, message: str) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            await self._socket.send(message)
        except ConnectionClosed as closed:
            raise RealtimeClosed(closed.rcvd.code if closed.rcvd else 1006) from closed

    async def recv(self) -> str:
        from websockets.exceptions import ConnectionClosed

        try:
            message = await self._socket.recv()
        except ConnectionClosed as closed:
            code, reason = (closed.rcvd.code, closed.rcvd.reason) if closed.rcvd else (1006, "")
            raise RealtimeClosed(code, reason) from closed
        return message if isinstance(message, str) else message.decode("utf-8", "replace")

    async def close(self) -> None:
        await self._socket.close()


def realtime_url(gateway_url: str, model: str) -> str:
    """`http(s)://gw/v1` → `ws(s)://gw/v1/realtime?model=…`."""
    parts = urlsplit(gateway_url.rstrip("/"))
    scheme = {"https": "wss", "http": "ws"}.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, f"{parts.path}/realtime", f"model={quote(model)}", ""))


# ───────────────────────────── publication ─────────────────────────────


async def require_realtime_gateway(session: AsyncSession, org_id: str) -> None:
    """A voice agent speaks through the gateway's realtime relay: on a gateway that has none, its
    version is refused when it is published, not discovered at the first call."""
    gateway = await session_gateway(session, org_id)
    if "realtime" not in getattr(gateway, "capabilities", frozenset()):
        raise unprocessable(
            "a voice agent needs a gateway that relays the realtime API",
            [
                {
                    "loc": ["runtime", "kind"],
                    "msg": f"the organisation's gateway (`{type(gateway).__name__}`) declares no "
                    "`realtime` capability",
                }
            ],
        )


# ───────────────────────────── screening the caller ─────────────────────────────


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str


def screen_client_event(event: dict[str, Any]) -> dict[str, Any] | Refusal:
    """What of a caller's event may reach the model: the event, or why it does not."""
    type_ = event.get("type")
    if type_ == "session.update":
        return Refusal(
            "session_update_refused",
            "the agent owns this session's configuration: `session.update` is refused",
        )
    if type_ in _RELAYED:
        return event
    if type_ == "response.create":
        if event.get("response"):
            return Refusal(
                "response_override_refused",
                "a response takes the agent's instructions and tools: `response.create` has no `response`",
            )
        return {"type": "response.create"}
    if type_ == "conversation.item.create":
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "user":
            return Refusal(
                "item_refused",
                "a caller adds only its own message: a tool's output or the agent's words come from "
                "the platform",
            )
        content = item.get("content")
        if not isinstance(content, list) or not all(
            isinstance(part, dict) and part.get("type") in _CALLER_CONTENT for part in content
        ):
            return Refusal("item_refused", "a caller's message carries `input_text` or `input_audio` only")
        return event
    return Refusal("event_refused", f"`{type_}` is not an event a caller sends in a voice session")


def error_event(refusal: Refusal, event_id: Any = None) -> str:
    error: dict[str, Any] = {
        "type": "invalid_request_error",
        "code": refusal.code,
        "message": refusal.message,
    }
    if event_id is not None:
        error["event_id"] = event_id
    return json.dumps({"type": "error", "error": error})


# ───────────────────────────── the relay ─────────────────────────────


def session_configuration(
    spec: AgentSpec, model: str, tools: dict[str, OutilDuCourtier], transcription_model: str
) -> dict[str, Any]:
    """The one `session.update` of a voice session: the agent's, never the caller's."""
    runtime: Any = spec.runtime
    audio_format = {"type": "audio/pcm", "rate": runtime.audio.sample_rate}
    audio_in: dict[str, Any] = {
        "format": audio_format,
        "turn_detection": {"type": "server_vad"} if runtime.turn_detection == "server_vad" else None,
    }
    if transcription_model:
        audio_in["transcription"] = {"model": transcription_model}
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": model,
            "instructions": spec.instructions,
            "audio": {"input": audio_in, "output": {"format": audio_format, "voice": runtime.voice}},
            "tools": [
                {
                    "type": "function",
                    "name": tool.nom,
                    "description": str(tool.annonce().get("description") or ""),
                    "parameters": tool_parameters(tool),
                }
                for tool in tools.values()
            ],
            "tool_choice": "auto",
        },
    }


@dataclass
class VoiceSession:
    """What the relay needs of an opened session, read once at the door."""

    session_id: str
    org_slug: str
    org_id: str
    project_id: str
    model: str
    key: str
    spec: AgentSpec
    tools: dict[str, OutilDuCourtier]
    max_seconds: float
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def write(self, act: Callable[[AsyncSession, AgentSessionRecord, Project], Awaitable[Any]]) -> Any:
        """One short transaction on the session's rows: a voice session lasts minutes, a database
        connection is not held for them. One at a time, so the journal's numbering holds."""
        async with self._lock, session_scope(self.org_slug) as db:
            record = await db.get(AgentSessionRecord, self.session_id)
            project = await db.get(Project, self.project_id)
            if record is None or project is None:
                return None
            return await act(db, record, project)

    async def note(self, type_: str, payload: dict[str, Any]) -> None:
        async def act(db: AsyncSession, record: AgentSessionRecord, _: Project) -> None:
            await journal(db, record, type_, payload)

        await self.write(act)


class CallerSocket(Protocol):
    """The caller's side, as the relay uses it: a Starlette `WebSocket` in the router."""

    async def receive(self) -> Mapping[str, Any]: ...
    async def send_text(self, data: str) -> None: ...


@dataclass
class Ending:
    reason: str
    code: int
    message: str = ""


async def relay(caller: CallerSocket, upstream: RealtimeLink, voice: VoiceSession) -> Ending:
    """Both directions until one ends: the caller hangs up, the model's socket closes, a frame is
    too large, the minutes run out, or the gateway refuses the key for budget."""
    transcription = get_settings().realtime_transcription_model
    configuration = session_configuration(voice.spec, voice.model, voice.tools, transcription)
    await upstream.send(json.dumps(configuration))
    greeting = getattr(voice.spec.runtime, "greeting", None)
    if greeting:
        await upstream.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {"instructions": f"Greet the caller with exactly these words: {greeting}"},
                }
            )
        )

    from_caller = asyncio.create_task(_from_caller(caller, upstream, voice))
    from_model = asyncio.create_task(_from_model(caller, upstream, voice))
    try:
        done, _ = await asyncio.wait(
            {from_caller, from_model}, timeout=voice.max_seconds, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (from_caller, from_model):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
    if not done:
        return Ending("max_minutes", 1000, "the agent's max_session_minutes is reached")
    return next(iter(done)).result()


async def _from_caller(caller: CallerSocket, upstream: RealtimeLink, voice: VoiceSession) -> Ending:
    while True:
        message = await caller.receive()
        if message["type"] == "websocket.disconnect":
            return Ending("closed", 1000)
        text = message.get("text")
        if text is None:
            if len(message.get("bytes") or b"") > MAX_FRAME_BYTES:
                return Ending("frame_too_large", 1009, f"a frame is limited to {MAX_FRAME_BYTES} bytes")
            await caller.send_text(error_event(Refusal("event_refused", "realtime events are JSON text")))
            continue
        if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
            await voice.note("client.refused", {"code": "frame_too_large", "bytes": len(text)})
            return Ending("frame_too_large", 1009, f"a frame is limited to {MAX_FRAME_BYTES} bytes")
        try:
            event = json.loads(text)
        except ValueError:
            event = None
        if not isinstance(event, dict):
            await caller.send_text(error_event(Refusal("event_refused", "an event is a JSON object")))
            continue
        screened = screen_client_event(event)
        if isinstance(screened, Refusal):
            await voice.note("client.refused", {"code": screened.code, "type": str(event.get("type"))[:64]})
            await caller.send_text(error_event(screened, event.get("event_id")))
            continue
        if screened["type"] == "conversation.item.create":
            said = [p.get("text", "") for p in screened["item"]["content"] if p.get("type") == "input_text"]
            if said:
                await voice.note("input.text", {"text": " ".join(said)[:2000]})
        try:
            await upstream.send(json.dumps(screened))
        except RealtimeClosed as closed:
            return Ending("upstream_closed", 1011, closed.reason or "the model's socket closed")


async def _from_model(caller: CallerSocket, upstream: RealtimeLink, voice: VoiceSession) -> Ending:
    while True:
        try:
            raw = await upstream.recv()
        except RealtimeClosed as closed:
            return Ending("upstream_closed", 1011, closed.reason or "the model's socket closed")
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        type_ = str(event.get("type"))
        if type_ in _SPOKEN:
            kind, key = _SPOKEN[type_]
            await voice.note(kind, {"text": str(event.get(key) or "")[:2000]})
        elif type_ == "response.done":
            await _count_the_response(voice, event.get("response") or {})
        elif type_ == "error" and budget_refusal(raw):
            await caller.send_text(raw)
            return Ending("budget_exceeded", 1008, "the session's budget is spent")
        await caller.send_text(raw)
        if type_ == "response.function_call_arguments.done":
            await _answer_the_call(upstream, voice, event)


async def _count_the_response(voice: VoiceSession, response: dict[str, Any]) -> None:
    async def counted(db: AsyncSession, record: AgentSessionRecord, _: Project) -> None:
        record.responses += 1
        payload = {"status": response.get("status"), "usage": response.get("usage") or {}}
        await journal(db, record, "response.done", payload)

    await voice.write(counted)


async def _answer_the_call(upstream: RealtimeLink, voice: VoiceSession, event: dict[str, Any]) -> None:
    name, call_id = str(event.get("name") or ""), event.get("call_id")
    tool = voice.tools.get(name)
    if tool is None:
        await voice.note("tool.refused", {"tool": name[:200], "reason": "not granted"})
        output = f"the tool `{name}` is not granted to this agent in this session"
    else:
        try:
            arguments = json.loads(event.get("arguments") or "{}")
        except ValueError:
            arguments = None
        if not isinstance(arguments, dict):
            output = "the arguments are not a JSON object"
        else:
            granted = tool

            async def call(db: AsyncSession, record: AgentSessionRecord, project: Project) -> str:
                return await call_session_tool(db, record, project, granted, arguments)

            output = str(await voice.write(call) or "")
    await upstream.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id, "output": output},
            }
        )
    )
    await upstream.send(json.dumps({"type": "response.create"}))


async def end_voice_session(voice: VoiceSession, reason: str) -> None:
    """The close of `agent_sessions`: the spend booked with `session_id`, the key revoked."""

    async def act(db: AsyncSession, record: AgentSessionRecord, _: Project) -> None:
        await close_session(db, record, reason=reason)

    await voice.write(act)
    log.info("agent_realtime.ended", session_id=voice.session_id, reason=reason)


__all__ = [
    "MAX_FRAME_BYTES",
    "CallerSocket",
    "Ending",
    "Refusal",
    "VoiceSession",
    "end_voice_session",
    "realtime_connector",
    "realtime_url",
    "relay",
    "require_realtime_gateway",
    "screen_client_event",
    "session_configuration",
    "set_realtime_connector",
]
