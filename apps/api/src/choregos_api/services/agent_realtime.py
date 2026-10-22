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

The spend is the relay's own (S41-15, `realtime_spend`): each `response.done` is priced from its
usage at the prices the session opened with, and the session closes with 1008 `budget_exceeded` the
moment the sum reaches its cap, the reservation or the per-minute rate of the catalogue.

A dropped socket is not a hang-up (S41-21, `VoiceRelay`): each event the caller gets is numbered,
the model's socket waits `RESUME_GRACE_S` for the caller to come back, and the events it missed are
sent again; past the grace the session closes, `caller_gone`. A close the caller sends hangs up.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

from choregos_adapters.base import RealtimeClosed, RealtimeConnector, RealtimeLink
from choregos_adapters.realtime_ws import WebsocketsConnector
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
from .realtime_spend import RealtimePrices, response_cost, session_cap

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
    return WebsocketsConnector(max_size=8 * MAX_FRAME_BYTES)


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


def error_payload(refusal: Refusal, event_id: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {
        "type": "invalid_request_error",
        "code": refusal.code,
        "message": refusal.message,
    }
    if event_id is not None:
        error["event_id"] = event_id
    return {"type": "error", "error": error}


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
    prices: RealtimePrices
    reserved_usd: float
    usd_per_minute: float | None = None
    #: A minute, in seconds: of the rate as of `max_seconds` (a test shortens both).
    minute_s: float = 60.0
    spent_usd: float = 0.0
    started: float = field(default_factory=time.monotonic)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def deadline(self) -> float:
        """When `max_session_minutes` ends the session, however many sockets it took."""
        return self.started + self.max_seconds

    def cap(self) -> float:
        return session_cap(
            self.reserved_usd, self.usd_per_minute, time.monotonic() - self.started, self.minute_s
        )

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


#: The close code of a socket that dropped without a close frame: the one cut a caller comes back
#: from. Any close the caller sends is a hang-up.
DROPPED = 1006
#: The platform's own event, the first on a session's socket: `{"type": …, "session_id": …}`.
SESSION_EVENT = "choregos.session"


class VoiceRelay:
    """One voice session's relay, which outlives its caller's socket (S41-21).

    Every event sent to the caller carries `seq`, its number in the session (1, 2, …), and the last
    `RESUME_BUFFER_BYTES` of them are held. When the caller's socket drops (`DROPPED`), the model's
    socket stays open for `grace_s`: its events are still priced, its function calls still answered,
    its events held. A caller that comes back in time with the session's id and the last `seq` it
    read gets the events after it, then the live ones; otherwise the session closes, `caller_gone`.
    Nothing is counted twice: a response is priced when the model sends it, never when it is resent.
    """

    def __init__(self, upstream: RealtimeLink, voice: VoiceSession, grace_s: float | None = None) -> None:
        self.upstream = upstream
        self.voice = voice
        self.grace_s = RESUME_GRACE_S if grace_s is None else grace_s
        self.seq = 0
        self.caller: CallerSocket | None = None
        self.detached_at = 0.0
        self._held: deque[tuple[int, str]] = deque()
        self._held_bytes = 0
        self._changed = asyncio.Event()
        self._final: Ending | None = None
        self._ended = asyncio.Event()
        self._owner: asyncio.Task[None] | None = None
        self._kick: asyncio.Event | None = None

    # ───────────────────────── its life ─────────────────────────

    async def start(self) -> None:
        """Configures the model's session, greets, and runs the relay until it ends."""
        transcription = get_settings().realtime_transcription_model
        configuration = session_configuration(
            self.voice.spec, self.voice.model, self.voice.tools, transcription
        )
        await self.upstream.send(json.dumps(configuration))
        greeting = getattr(self.voice.spec.runtime, "greeting", None)
        if greeting:
            instructions = f"Greet the caller with exactly these words: {greeting}"
            await self.upstream.send(
                json.dumps({"type": "response.create", "response": {"instructions": instructions}})
            )
        _RELAYS[self.voice.session_id] = self
        # The caller's first event: the session's id, to come back with if its socket drops.
        await self.send({"type": SESSION_EVENT, "session_id": self.voice.session_id})
        self._owner = asyncio.create_task(self._own())

    async def done(self) -> Ending:
        """The relay's ending, once the session is closed and its upstream with it."""
        if self._owner is not None:
            await asyncio.shield(self._owner)
        return self._final or Ending("relay_error", 1011, "the relay failed")

    def _finish(self, ending: Ending) -> None:
        if self._final is None:
            self._final = ending
            self._ended.set()
            self._changed.set()

    async def _own(self) -> None:
        try:
            await self._serve()
        except Exception as failure:  # the session still closes, its spend booked
            log.warning(
                "agent_realtime.relay_failed", session_id=self.voice.session_id, error=str(failure)[:300]
            )
            self._finish(Ending("relay_error", 1011, "the relay failed"))
        finally:
            _RELAYS.pop(self.voice.session_id, None)
            with contextlib.suppress(Exception):
                await self.upstream.close()
            ending = self._final or Ending("relay_error", 1011, "the relay failed")
            await end_voice_session(self.voice, ending.reason)

    async def _serve(self) -> None:
        """Until the model ends, the minutes run out, or the caller stays away past the grace."""
        model = asyncio.create_task(self._from_model())
        try:
            while self._final is None:
                now = time.monotonic()
                left = self.voice.deadline() - now
                if left <= 0:
                    self._finish(Ending("max_minutes", 1000, "the agent's max_session_minutes is reached"))
                    break
                wait = left
                if self.caller is None and self.detached_at:
                    grace_left = self.detached_at + self.grace_s - now
                    if grace_left <= 0:
                        self._finish(Ending("caller_gone", 1001, "the caller did not come back in time"))
                        break
                    wait = min(wait, grace_left)
                changed = asyncio.ensure_future(self._changed.wait())
                done, _ = await asyncio.wait(
                    {model, changed}, timeout=wait, return_when=asyncio.FIRST_COMPLETED
                )
                changed.cancel()
                self._changed.clear()
                if model in done:
                    self._finish(model.result())
        finally:
            if not model.done():
                model.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await model

    # ───────────────────────── its caller ─────────────────────────

    def refusal(self, after: int) -> Ending | None:
        """Why a caller cannot resume after `after`: an event it never had, or one no longer held."""
        if after > self.seq or (after < self.seq and (not self._held or self._held[0][0] > after + 1)):
            return Ending("resume_refused", 1008, f"the events after {after} are not held anymore")
        return None

    async def attach(self, caller: CallerSocket, after: int = 0) -> Ending | None:
        """Serves `caller` from the event after `after` until its socket ends: the ending to close
        it with, or `None` when it dropped and the relay waits for it to come back."""
        if self._final is not None:
            return await self.done()
        refused = self.refusal(after)
        if refused is not None:
            return refused
        if self._kick is not None:
            self._kick.set()  # a socket the server has not seen drop yet: the new one takes its place
            await self.voice.note("caller.replaced", {"seq": self.seq})
        kick = self._kick = asyncio.Event()
        resumed = self.caller is not None or self.detached_at > 0
        self.caller, self.detached_at = caller, 0.0
        self._changed.set()
        if resumed:
            await self.voice.note("caller.resumed", {"after": after, "seq": self.seq})
        for seq, text in list(self._held):
            if seq > after:
                await caller.send_text(text)
        reader = asyncio.create_task(self._from_caller(caller))
        waits = {reader, asyncio.ensure_future(self._ended.wait()), asyncio.ensure_future(kick.wait())}
        try:
            done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in waits:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
        if kick.is_set():
            return Ending("replaced", 1000, "the session was resumed on another socket")
        if reader in done:
            ending = reader.result()
            if ending.reason == "dropped":
                self.caller, self.detached_at, self._kick = None, time.monotonic(), None
                self._changed.set()
                await self.voice.note("caller.dropped", {"seq": self.seq})
                return None
            self._finish(ending)
        return await self.done()

    async def send(self, event: dict[str, Any]) -> None:
        """An event to the caller, numbered and held; to nobody while the caller is away."""
        self.seq += 1
        numbered = json.dumps({**event, "seq": self.seq})
        self._held.append((self.seq, numbered))
        self._held_bytes += len(numbered)
        while self._held_bytes > RESUME_BUFFER_BYTES and len(self._held) > 1:
            self._held_bytes -= len(self._held.popleft()[1])
        caller = self.caller
        if caller is not None:
            with contextlib.suppress(Exception):  # a socket going away: its reader sees it
                await caller.send_text(numbered)

    async def _from_caller(self, caller: CallerSocket) -> Ending:
        upstream, voice = self.upstream, self.voice
        while True:
            message = await caller.receive()
            if message["type"] == "websocket.disconnect":
                if message.get("code") == DROPPED:
                    return Ending("dropped", DROPPED)
                return Ending("closed", 1000)
            text = message.get("text")
            if text is None:
                if len(message.get("bytes") or b"") > MAX_FRAME_BYTES:
                    return Ending("frame_too_large", 1009, f"a frame is limited to {MAX_FRAME_BYTES} bytes")
                await self.send(error_payload(Refusal("event_refused", "realtime events are JSON text")))
                continue
            if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
                await voice.note("client.refused", {"code": "frame_too_large", "bytes": len(text)})
                return Ending("frame_too_large", 1009, f"a frame is limited to {MAX_FRAME_BYTES} bytes")
            try:
                event = json.loads(text)
            except ValueError:
                event = None
            if not isinstance(event, dict):
                await self.send(error_payload(Refusal("event_refused", "an event is a JSON object")))
                continue
            screened = screen_client_event(event)
            if isinstance(screened, Refusal):
                await voice.note(
                    "client.refused", {"code": screened.code, "type": str(event.get("type"))[:64]}
                )
                await self.send(error_payload(screened, event.get("event_id")))
                continue
            if screened["type"] == "conversation.item.create":
                said = [
                    p.get("text", "") for p in screened["item"]["content"] if p.get("type") == "input_text"
                ]
                if said:
                    await voice.note("input.text", {"text": " ".join(said)[:2000]})
            try:
                await upstream.send(json.dumps(screened))
            except RealtimeClosed as closed:
                return Ending("upstream_closed", 1011, closed.reason or "the model's socket closed")

    # ───────────────────────── its model ─────────────────────────

    async def _from_model(self) -> Ending:
        upstream, voice = self.upstream, self.voice
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
            spent: Ending | None = None
            if type_ in _SPOKEN:
                kind, key = _SPOKEN[type_]
                await voice.note(kind, {"text": str(event.get(key) or "")[:2000]})
            elif type_ == "response.done":
                spent = await _count_the_response(voice, event.get("response") or {})
            elif type_ == "error" and budget_refusal(raw):
                await self.send(event)
                return Ending("budget_exceeded", 1008, "the session's budget is spent")
            await self.send(event)
            if spent is not None:
                return spent
            if type_ == "response.function_call_arguments.done":
                await _answer_the_call(upstream, voice, event)


#: The relays this API process holds, by session: where a caller's reconnection finds its session.
_RELAYS: dict[str, VoiceRelay] = {}
#: How long the model's socket waits for a caller whose socket dropped (a test shortens it).
RESUME_GRACE_S = 30.0
#: What a relay holds of the events it sent, for a caller coming back: about a minute of the
#: model's audio, base64-encoded.
RESUME_BUFFER_BYTES = 8 * 1024 * 1024


def held_relay(session_id: str) -> VoiceRelay | None:
    """The relay of that session, if this process holds it."""
    return _RELAYS.get(session_id)


async def _count_the_response(voice: VoiceSession, response: dict[str, Any]) -> Ending | None:
    """Journals the response with its price; past the cap, the ending of the session."""
    usage = response.get("usage") or {}
    cost = response_cost(usage if isinstance(usage, dict) else {}, voice.prices)
    voice.spent_usd = round(voice.spent_usd + cost.cost_usd, 8)
    cap = voice.cap()

    async def counted(db: AsyncSession, record: AgentSessionRecord, _: Project) -> None:
        record.responses += 1
        payload = {
            "status": response.get("status"),
            "usage": usage,
            "priced_by": "relay",
            "cost_usd": cost.cost_usd,
            "tokens_in": cost.tokens_in,
            "tokens_out": cost.tokens_out,
            "tokens_cached": cost.tokens_cached,
            "spent_usd": voice.spent_usd,
            "cap_usd": round(cap, 8),
        }
        await journal(db, record, "response.done", payload)

    await voice.write(counted)
    if voice.spent_usd < cap:
        return None
    log.info(
        "agent_realtime.cap_reached", session_id=voice.session_id, spent_usd=voice.spent_usd, cap_usd=cap
    )
    return Ending("budget_exceeded", 1008, f"the session spent {voice.spent_usd:.4f} of its {cap:.4f} USD")


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
    "RESUME_BUFFER_BYTES",
    "RESUME_GRACE_S",
    "SESSION_EVENT",
    "CallerSocket",
    "Ending",
    "Refusal",
    "VoiceRelay",
    "VoiceSession",
    "end_voice_session",
    "error_payload",
    "held_relay",
    "realtime_connector",
    "realtime_url",
    "require_realtime_gateway",
    "screen_client_event",
    "session_configuration",
    "set_realtime_connector",
]
