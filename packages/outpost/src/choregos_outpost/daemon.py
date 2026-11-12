# SPDX-License-Identifier: Apache-2.0
"""The daemon: it polls the door, decides each call alone, makes it, and answers (ADRs 0147-0149).

**One poll at a time.** A single loop holds the one connection the instance keeps: it polls, takes
what the answer hands over, and polls again at once. A poll announces how many more calls the
daemon can take; with no room left it asks without waiting, to learn the cancellations, and comes
back when a call has ended. A failure is followed by a wait that doubles, with jitter.

**One decision per call id** (ADR 0148 §13). For each call of an answer, in this order:

1. the journal is consulted first. An id it holds gets what it holds: the same refusal, the answer
   it kept, or nothing more when the call is under way;
2. the door's signature is checked against the keys the policy file pins, before the envelope is
   opened. A failure is `signature_refused`: the envelope stays closed, the server is not called,
   and the one audit line names no tool;
3. the envelope is opened and the policy is applied, deny by default: an unknown kind, a deadline
   that is behind (by the daemon's own clock), an unknown alias, a tool outside its patterns, a
   payload above the ceiling, no room left. Then, from the endpoint's own server: a tool it does
   not declare read-only where writes are not allowed;
4. the call is made, and its answer is sealed to the `answer_key` found inside the call.

**What is recorded comes first.** The audit line `received` and the journal's records are on disk
before the daemon acts on them, and a write's id is recorded before the write is executed. When
the disk refuses a record, the call it was for is refused `busy` and its server receives nothing;
the line that was lost is written to the daemon's own log; the daemon stops polling, lets what it
holds finish, and exits with `Exit.CANNOT_RECORD`. An outpost that cannot record does not serve.

**Cancellation.** A call the door cancels before the daemon started it is refused `cancelled`. A
read that is in flight is abandoned. A write that has begun is left to finish: stopping the
request would not undo it, and the operator's log should say what it did.

**Stopping.** `stop()` ends the polling; what the daemon holds has `grace_s` to finish and answer.
A call still running after that is left as the journal has it, and the next start reports it.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import functools
import random
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import asdict, dataclass, field
from typing import Any

from choregos_adapters import sealing
from choregos_adapters.mcp import McpTool
from choregos_contracts.enums import (
    OutpostAnswerOutcome,
    OutpostAuditEvent,
    OutpostCallKind,
    OutpostDoorCode,
    OutpostRefusalCode,
)
from choregos_contracts.outpost import (
    MAX_BODY_BYTES,
    MAX_WAIT_S,
    OutpostAnswer,
    OutpostCall,
    OutpostEndpoint,
    OutpostLoggedNames,
    OutpostPolicy,
    OutpostPollAnswer,
    OutpostProtocolWindow,
    OutpostSealedCall,
    OutpostTool,
    arguments_sha256,
)
from pydantic import ValidationError

from . import PROTOCOL
from .audit import AuditLog, AuditWriteError, logged_names
from .calls import signed_by
from .identity import Identity
from .journal import BEGUN, DONE, REFUSED, UNOPENED_KEEP_S, Entry, Journal, JournalError
from .logs import get_logger
from .policy import allows, ceiling, weight
from .toolhost import EndpointUnavailableError, Made, ResultTooLargeError, ToolHost
from .uplink import Backoff, DoorError, DoorRefusedError, DoorUnreachableError, Uplink

log = get_logger()

#: How long the daemon waits for a call to end before it asks again, when it has no room left.
FULL_PAUSE_S = 5.0
#: The journal is swept this often.
SWEEP_EVERY_S = 30.0
#: The most tools a listing's answer carries (`outpost-answer.schema.json`).
MAX_TOOLS = 1024
#: The longest the daemon waits on the door's word (`retry_after`) before it asks again.
MAX_RETRY_AFTER_S = 300.0
#: `{"sealed":""}`: what the answer's body adds around the envelope.
_BODY_AROUND = 13
_CHECKING, _SERVING, _ANSWERING = "checking", "serving", "answering"
#: Refusals of the door that say a call is no longer this instance's to answer: it is dropped.
_SETTLED = (OutpostDoorCode.NOT_FOUND, OutpostDoorCode.LEASE_LOST)
#: Refusals that sending the same body again would not change.
_FINAL = (OutpostDoorCode.INVALID_REQUEST, OutpostDoorCode.BODY_TOO_LARGE)


class Exit(enum.IntEnum):
    """Why `run` ended: the process's exit code."""

    #: It was asked to stop.
    STOPPED = 0
    #: The door serves a protocol above this daemon's: it stopped polling. Upgrade it.
    UPGRADE_REQUIRED = 3
    #: The instance, or its outpost, was revoked.
    REVOKED = 4
    #: The disk refused the journal or the audit log: nothing more is executed.
    CANNOT_RECORD = 5


class _StoppedError(Exception):
    """The daemon was asked to stop while it waited."""


class _RefusalError(Exception):
    def __init__(self, code: OutpostRefusalCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class Facts:
    """What the audit log and the journal say of a call: names and digests, never a payload."""

    signed_by: str | None = None
    kind: str | None = None
    endpoint: str | None = None
    tool: str | None = None
    arguments_sha256: str | None = None
    asserted: OutpostLoggedNames = field(default_factory=OutpostLoggedNames)

    def names(self) -> dict[str, Any]:
        return {name: value for name, value in asdict(self).items() if name != "asserted"}

    def line(self) -> dict[str, Any]:
        return {**self.names(), "asserted": self.asserted}

    @classmethod
    def of(cls, entry: Entry) -> Facts:
        return cls(entry.signed_by, entry.kind, entry.endpoint, entry.tool, entry.arguments_sha256)


class Daemon:
    def __init__(  # noqa: PLR0913 - one keyword per part of the daemon
        self,
        *,
        policy: OutpostPolicy,
        identity: Identity,
        instance_id: str,
        uplink: Uplink,
        journal: Journal,
        audit: AuditLog,
        tools: ToolHost,
        backoff: Backoff | None = None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        grace_s: float = 20.0,
        full_pause_s: float = FULL_PAUSE_S,
    ) -> None:
        self.policy = policy
        self.identity = identity
        self.instance_id = instance_id
        self.uplink = uplink
        self.journal = journal
        self.audit = audit
        self.tools = tools
        self.backoff = backoff or Backoff()
        self.grace_s = grace_s
        self.full_pause_s = full_pause_s
        self._now = now
        self._sleep = sleep
        self._calls: dict[str, asyncio.Task[None]] = {}
        self._phase: dict[str, str] = {}
        self._writes: set[str] = set()
        self._cancelled: set[str] = set()
        self._sending: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._exit: Exit | None = None
        self._paused = False
        self._swept = 0.0
        self._said: set[str] = set()
        self._jitter = random.Random()  # noqa: S311 - jitter, not a secret

    # ───────────────────────── the loop ─────────────────────────

    def stop(self) -> None:
        """Ends the polling; what the daemon holds may finish within `grace_s`."""
        self._stop.set()

    async def run(self) -> Exit:
        log.info(
            "outpost.started",
            instance=self.instance_id,
            fingerprint=self.identity.fingerprint,
            control_plane=self.uplink.control_plane,
            protocol=PROTOCOL,
            endpoints=sorted(self.policy.endpoints),
            concurrency=self.policy.concurrency,
        )
        try:
            await self._recover()
            while self._exit is None and not self._stop.is_set():
                await self._cycle()
        except _StoppedError:
            pass
        finally:
            await self._drain()
        result = self._exit if self._exit is not None else Exit.STOPPED
        log.info("outpost.stopped", reason=result.name.lower())
        return result

    async def _cycle(self) -> None:
        await self._sweep()
        free = max(0, self.policy.concurrency - len(self._calls))
        poll = self.uplink.poll(
            wait_s=MAX_WAIT_S if free else 0,
            capacity=free,
            endpoints=sorted(self.policy.endpoints),
            running=sorted(self._calls),
        )
        try:
            answer = await self._unless_stopped(poll)
        except DoorRefusedError as refused:
            await self._refused(refused)
            return
        except DoorUnreachableError as error:
            if error.status == 426:
                self._upgrade_required(None)
                return
            wait = self.backoff.next()
            log.warning("door.unreachable", reason=str(error), retry_in_s=round(wait, 1))
            await self._nap(wait)
            return
        self.backoff.reset()
        self._said.discard("pending")
        if self.uplink.sequence_fault and "sequence" not in self._said:
            self._said.add("sequence")
            log.warning("outpost.sequence_not_kept", reason=self.uplink.sequence_fault)
        await self._take(answer)
        if not free and self._calls and self._exit is None:
            # No room: come back when a call has ended, or soon, to hear the cancellations.
            with contextlib.suppress(_StoppedError):
                await self._unless_stopped(asyncio.wait(set(self._calls.values()), timeout=self.full_pause_s))

    async def _unless_stopped[Result](self, awaited: Coroutine[Any, Any, Result]) -> Result:
        """Awaits `awaited`, and gives up on it as soon as the daemon is asked to stop."""
        task = asyncio.ensure_future(awaited)
        stopped = asyncio.ensure_future(self._stop.wait())
        try:
            await asyncio.wait({task, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                return task.result()
            raise _StoppedError
        finally:
            stopped.cancel()
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, DoorError):
                    await task

    async def _nap(self, seconds: float) -> None:
        if self._sleep is not None:
            await self._sleep(seconds)
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    def _judged_now(self) -> float:
        """The time a deadline is judged by: the daemon's own clock. The door's time is not signed;
        when it is ahead it brings a deadline closer, and it never pushes one away."""
        return self._now() + max(0.0, self.uplink.drift() or 0.0)

    async def _sweep(self) -> None:
        if self._now() - self._swept < SWEEP_EVERY_S:
            return
        self._swept = self._now()
        await self._journalled(self.journal.sweep(self._judged_now()))

    async def _drain(self) -> None:
        """Lets what the daemon holds finish and answer, for `grace_s` at most."""
        held = {*self._calls.values(), *self._sending}
        if held:
            _, late = await asyncio.wait(held, timeout=self.grace_s)
            # A call that spawned its answer while we waited: one more look, then the rest is cut.
            late |= {task for task in self._sending if not task.done()}
            for task in late:
                task.cancel()
            if late:
                log.warning("outpost.left_running", calls=sorted(self._calls))
                await asyncio.gather(*late, return_exceptions=True)

    # ───────────────────────── what the door says ─────────────────────────

    async def _refused(self, refused: DoorRefusedError) -> None:
        """A problem of the door on a poll: stop for good, or say why and wait."""
        problem, code = refused.problem, refused.code
        if code is OutpostDoorCode.PROTOCOL_UNSUPPORTED or refused.status == 426:
            self._upgrade_required(problem.protocol)
            return
        if code is OutpostDoorCode.INSTANCE_REVOKED:
            log.error("outpost.revoked", detail="the instance, or its outpost, was revoked: the daemon stops")
            self._exit = Exit.REVOKED
            return
        wait = max(self.backoff.next(), float(min(problem.retry_after or 0, MAX_RETRY_AFTER_S)))
        if code is OutpostDoorCode.INSTANCE_PENDING:
            wait = min(wait, 30.0)
            if "pending" not in self._said:
                self._said.add("pending")
                log.info(
                    "outpost.pending",
                    detail="nobody approved this instance yet: compare this fingerprint in the console",
                    fingerprint=self.identity.fingerprint,
                )
        elif code is OutpostDoorCode.STALE_IAT:
            log.error(
                "outpost.clock_drift",
                detail="the door refuses this machine's clock: set it (the door's own is `server_time`)",
                drift_s=round(self.uplink.drift() or 0.0, 1),
                server_time=problem.server_time.isoformat(),
            )
        elif code is OutpostDoorCode.POLL_CONFLICT:
            log.warning(
                "door.poll_conflict",
                detail="another poll waits under this instance's key: a former poll of this daemon that "
                "the door still holds, or a second holder of the key",
            )
        else:
            log.error("door.refused", code=problem.code, status=problem.status, detail=problem.detail)
        await self._nap(wait)

    def _upgrade_required(self, window: OutpostProtocolWindow | None) -> None:
        log.error(
            "outpost.protocol_unsupported",
            detail="the door no longer serves this daemon's protocol: it has stopped polling. Upgrade the "
            "daemon; nobody does it for its operator.",
            speaks=PROTOCOL,
            door_minimum=window.minimum if window else None,
            door_current=window.current if window else None,
        )
        self._exit = Exit.UPGRADE_REQUIRED

    def _cannot_record(self, reason: str) -> None:
        if self._exit is None:
            log.error(
                "outpost.cannot_record",
                detail="the disk refused a record: nothing more is executed, and the daemon stops",
                reason=reason,
            )
            self._exit = Exit.CANNOT_RECORD

    async def _take(self, answer: OutpostPollAnswer) -> None:
        if answer.stop != self._paused:
            self._paused = answer.stop
            log.info("outpost.paused" if answer.stop else "outpost.resumed")
        for call_id in answer.cancel:
            self._cancel(call_id)
        # At most 64 calls, by the contract: an answer that carries more is not read at all (`Uplink`).
        for call in answer.calls:
            await self._receive(call)
        if answer.protocol.minimum > PROTOCOL:
            self._upgrade_required(answer.protocol)

    def _cancel(self, call_id: str) -> None:
        task = self._calls.get(call_id)
        if task is None:
            return
        phase = self._phase.get(call_id)
        if phase == _ANSWERING or (phase == _SERVING and call_id in self._writes):
            # A write that has begun is left to finish: its answer will meet `lease_lost`.
            log.info("call.cancel_too_late", call_id=call_id)
            return
        self._cancelled.add(call_id)
        task.cancel()

    # ───────────────────────── recording ─────────────────────────

    async def _line(self, event: OutpostAuditEvent, call_id: str, **fields: Any) -> bool:
        """Writes an audit line. A line the disk refuses is not lost in silence: it goes to the
        daemon's own log, and the daemon stops serving."""
        try:
            await self.audit.append(event, call_id, **fields)
        except AuditWriteError as error:
            log.error("audit.line_lost", line=error.line, reason=str(error))
            self._cannot_record(str(error))
            return False
        return True

    async def _journalled(self, step: Awaitable[Any]) -> bool:
        try:
            await step
        except JournalError as error:
            self._cannot_record(str(error))
            return False
        return True

    # ───────────────────────── one call ─────────────────────────

    async def _receive(self, handed: OutpostSealedCall) -> None:
        now = self._judged_now()
        entry = self.journal.standing(handed.id, now)
        if entry is not None:
            await self._again(entry, now)
            return
        key = signed_by(handed, pinned=self.policy.door_keys, instance_id=self.instance_id)
        if key is None:
            # Not opened, not made: one line, which names nothing the envelope held.
            await self._refuse(
                handed.id, OutpostRefusalCode.SIGNATURE_REFUSED, Facts(), now + UNOPENED_KEEP_S
            )
            return
        call = self._opened(handed)
        facts = self._facts(key, call)
        keep_until = call.deadline.timestamp() if call and call.deadline else now + UNOPENED_KEEP_S
        if not await self._line(OutpostAuditEvent.RECEIVED, handed.id, **facts.line()):
            # What was not recorded as received is not made.
            self._send(handed.id, code=OutpostRefusalCode.CANNOT_RECORD, until=keep_until)
            return
        if call is None:
            await self._refuse(handed.id, OutpostRefusalCode.CALL_UNREADABLE, facts, keep_until)
            return
        code = self._refusal_of(call, now)
        if code is not None:
            await self._refuse(call.id, code, facts, keep_until)
            return
        taken = self.journal.take(call.id, keep_until=keep_until, answer_key=call.answer_key, **facts.names())
        if not await self._journalled(taken):
            await self._line(
                OutpostAuditEvent.REFUSED, call.id, refusal=OutpostRefusalCode.CANNOT_RECORD, **facts.line()
            )
            self._send(call.id, code=OutpostRefusalCode.CANNOT_RECORD, until=keep_until)
            return
        self._phase[call.id] = _CHECKING
        task = asyncio.create_task(self._make(call, facts, keep_until))
        self._calls[call.id] = task
        task.add_done_callback(functools.partial(self._ended, call.id))

    def _opened(self, handed: OutpostSealedCall) -> OutpostCall | None:
        """The call an envelope holds, or None when it does not open or is not this call."""
        try:
            call = OutpostCall.model_validate(self.identity.open(handed.id, handed.sealed))
        except (sealing.SealError, ValidationError):
            return None
        return call if call.id == handed.id else None

    @staticmethod
    def _facts(key: str, call: OutpostCall | None) -> Facts:
        if call is None:
            return Facts(signed_by=key)
        digest = arguments_sha256(call.arguments) if call.arguments is not None else None
        return Facts(key, call.kind, call.endpoint, call.tool, digest, logged_names(call.asserted))

    def _refusal_of(self, call: OutpostCall, now: float) -> OutpostRefusalCode | None:
        """What the policy file alone refuses, before anything is asked of a server."""
        kind = call.known_kind
        if kind is None:
            return OutpostRefusalCode.UNSUPPORTED_KIND
        assert call.deadline is not None and call.endpoint is not None  # a known kind is complete
        if call.deadline.timestamp() <= now:
            return OutpostRefusalCode.DEADLINE_PASSED
        endpoint = self.policy.endpoints.get(call.endpoint)
        if endpoint is None:
            return OutpostRefusalCode.UNKNOWN_ENDPOINT
        if kind is OutpostCallKind.CALL_TOOL:
            if not allows(endpoint, call.tool or ""):
                return OutpostRefusalCode.TOOL_NOT_ALLOWED
            if weight(call.arguments) > ceiling(endpoint):
                return OutpostRefusalCode.PAYLOAD_TOO_LARGE
        if len(self._calls) >= self.policy.concurrency or self._stop.is_set() or self._exit is not None:
            return OutpostRefusalCode.BUSY
        return None

    async def _refuse(
        self,
        call_id: str,
        code: OutpostRefusalCode,
        facts: Facts,
        keep_until: float,
        *,
        write: bool | None = None,
    ) -> None:
        """Records a refusal, writes its audit line, and tells the door. The server received nothing."""
        await self._journalled(
            self.journal.refuse(call_id, code, keep_until=keep_until, write=write, **facts.names())
        )
        await self._line(OutpostAuditEvent.REFUSED, call_id, refusal=code, write=write, **facts.line())
        log.info("call.refused", call_id=call_id, refusal=code.value, endpoint=facts.endpoint)
        self._send(call_id, code=code, until=keep_until)

    async def _again(self, entry: Entry, now: float) -> None:
        """An envelope under an id the journal holds: it gets what the journal holds."""
        facts = Facts.of(entry)
        if entry.under_way:
            if entry.id in self._calls:
                # A copy of a call that has no answer yet: said, with no outcome to repeat.
                await self._line(OutpostAuditEvent.REPLAYED, entry.id, **facts.line())
                log.info("call.already_under_way", call_id=entry.id)
            else:
                await self._left_in_the_middle(entry, now)
            return
        if entry.state == REFUSED and entry.refusal is not None:
            code = OutpostRefusalCode(entry.refusal)
            await self._line(
                OutpostAuditEvent.REFUSED, entry.id, refusal=code, write=entry.write, **facts.line()
            )
            self._send(entry.id, code=code, until=entry.keep_until)
            return
        sealed = await self.journal.answer(entry.id) if entry.state == DONE else None
        if sealed is not None and entry.outcome is not None:
            await self._line(
                OutpostAuditEvent.REPLAYED,
                entry.id,
                outcome=OutpostAnswerOutcome(entry.outcome),
                write=entry.write,
                **facts.line(),
            )
            log.info("call.replayed", call_id=entry.id, outcome=entry.outcome)
            self._send(entry.id, sealed=sealed, until=entry.keep_until)
            return
        # A call that was made, whose answer is no longer held: its deadline is behind. It is not
        # made again, whatever the clock says today.
        code = OutpostRefusalCode.DEADLINE_PASSED
        await self._line(OutpostAuditEvent.REFUSED, entry.id, refusal=code, write=entry.write, **facts.line())
        self._send(entry.id, code=code, until=entry.keep_until)

    def _left(self, endpoint: OutpostEndpoint, keep_until: float) -> float:
        """How long a server is waited for: the endpoint's timeout, or what is left before the deadline."""
        return max(0.0, min(float(endpoint.timeout_s), keep_until - self._judged_now()))

    async def _make(self, call: OutpostCall, facts: Facts, keep_until: float) -> None:
        assert call.endpoint is not None
        endpoint = self.policy.endpoints[call.endpoint]
        write: bool | None = None
        try:
            if call.known_kind is OutpostCallKind.CALL_TOOL:
                write = await self._is_write(call, endpoint, keep_until)
                if write and endpoint.writes != "allow":
                    raise _RefusalError(OutpostRefusalCode.WRITE_DENIED)
            if self._left(endpoint, keep_until) <= 0:
                raise _RefusalError(OutpostRefusalCode.DEADLINE_PASSED)
            # Recorded before the server is called: a write is never executed on a journal that
            # does not hold its id.
            if not await self._journalled(self.journal.begin(call.id, write=bool(write))):
                raise _RefusalError(OutpostRefusalCode.CANNOT_RECORD)
            self._phase[call.id] = _SERVING
            if write:
                self._writes.add(call.id)
            made, tools = await self._serve(call, endpoint, keep_until)
        except _RefusalError as refusal:
            await self._refuse(call.id, refusal.code, facts, keep_until, write=write)
            return
        except EndpointUnavailableError as error:
            log.warning("endpoint.unavailable", call_id=call.id, endpoint=call.endpoint, reason=str(error))
            await self._refuse(
                call.id, OutpostRefusalCode.ENDPOINT_UNAVAILABLE, facts, keep_until, write=write
            )
            return
        except asyncio.CancelledError:
            if call.id not in self._cancelled:
                raise  # the daemon is stopping: the journal says where this call was
            await self._cancelled_call(call, facts, keep_until, write)
            return
        self._phase[call.id] = _ANSWERING
        await self._answer(call, facts, keep_until, write, made, tools)

    async def _is_write(self, call: OutpostCall, endpoint: OutpostEndpoint, keep_until: float) -> bool:
        """Whether the tool writes, read from the outpost's own server and never from the call
        (ADR 0147 §6): a tool its server does not declare read-only is a write."""
        assert call.endpoint is not None
        try:
            listed = await self.tools.listing(call.endpoint, within_s=self._left(endpoint, keep_until))
        except ResultTooLargeError as error:
            raise EndpointUnavailableError("its tool listing is above what the outpost reads") from error
        declared = next((tool for tool in listed if tool.name == call.tool), None)
        return declared is None or not declared.read_only

    async def _serve(
        self, call: OutpostCall, endpoint: OutpostEndpoint, keep_until: float
    ) -> tuple[Made, list[OutpostTool] | None]:
        assert call.endpoint is not None
        budget = self._left(endpoint, keep_until)
        if call.known_kind is OutpostCallKind.CALL_TOOL:
            made = await self.tools.call(
                call.endpoint, call.tool or "", call.arguments or {}, within_s=budget
            )
            return made, None
        started = time.monotonic()

        def listing(outcome: OutpostAnswerOutcome) -> Made:
            return Made(outcome, int((time.monotonic() - started) * 1000))

        try:
            listed = await self.tools.listing(call.endpoint, within_s=budget)
        except ResultTooLargeError:
            return listing(OutpostAnswerOutcome.OVERSIZED), None
        tools = [_tool(tool) for tool in listed if _lets_through(endpoint, tool)]
        if len(tools) > MAX_TOOLS or weight([tool.model_dump() for tool in tools]) > ceiling(endpoint):
            return listing(OutpostAnswerOutcome.OVERSIZED), None
        return listing(OutpostAnswerOutcome.RESULT), tools

    async def _cancelled_call(
        self, call: OutpostCall, facts: Facts, keep_until: float, write: bool | None
    ) -> None:
        if self._phase.get(call.id) != _SERVING:
            await self._refuse(call.id, OutpostRefusalCode.CANCELLED, facts, keep_until, write=write)
            return
        # A read that was in flight: sent, and no answer held. The door waits for none.
        outcome = OutpostAnswerOutcome.UNANSWERED
        await self._journalled(self.journal.finish(call.id, outcome, sealed=None))
        await self._line(OutpostAuditEvent.EXECUTED, call.id, outcome=outcome, write=write, **facts.line())
        log.info("call.abandoned", call_id=call.id)

    async def _answer(
        self,
        call: OutpostCall,
        facts: Facts,
        keep_until: float,
        write: bool | None,
        made: Made,
        tools: list[OutpostTool] | None,
    ) -> None:
        answer = OutpostAnswer(
            id=call.id,
            outcome=made.outcome,
            result=made.result,
            tools=tools,
            error=made.error,
            write=write,
            duration_ms=made.duration_ms,
        )
        first = self._sealed(call.answer_key, answer)
        if first is not None and len(first) + _BODY_AROUND > MAX_BODY_BYTES["answer"]:
            # Above the door's cap: the call was executed, and its result is not sent.
            answer = OutpostAnswer(
                id=call.id, outcome=OutpostAnswerOutcome.OVERSIZED, write=write, duration_ms=made.duration_ms
            )
            first = self._sealed(call.answer_key, answer)
        # What an id seen again is answered with: the same answer, marked as the journal's.
        again = self._sealed(call.answer_key, answer.model_copy(update={"replayed": True}))
        await self._journalled(self.journal.finish(call.id, answer.outcome, sealed=again))
        await self._line(
            OutpostAuditEvent.EXECUTED,
            call.id,
            outcome=answer.outcome,
            write=write,
            duration_ms=answer.duration_ms,
            **facts.line(),
        )
        log.info(
            "call.executed",
            call_id=call.id,
            endpoint=call.endpoint,
            outcome=answer.outcome.value,
            duration_ms=answer.duration_ms,
        )
        if first is not None:
            await self._deliver(call.id, sealed=first, code=None, until=keep_until)

    @staticmethod
    def _sealed(answer_key: str | None, answer: OutpostAnswer) -> str | None:
        """An answer sealed to the key found inside the call it answers (ADR 0148 §5)."""
        if answer_key is None:
            return None
        try:
            return sealing.seal(answer_key, answer.id, answer.model_dump(mode="json", exclude_none=True))
        except (ValueError, TypeError):
            log.error("call.answer_key_unusable", call_id=answer.id)
            return None

    def _ended(self, call_id: str, task: asyncio.Task[None]) -> None:
        self._calls.pop(call_id, None)
        self._phase.pop(call_id, None)
        self._writes.discard(call_id)
        self._cancelled.discard(call_id)
        if not task.cancelled() and task.exception() is not None:
            log.error("call.failed", call_id=call_id, error=repr(task.exception()))

    # ───────────────────────── telling the door ─────────────────────────

    def _send(
        self, call_id: str, *, until: float, sealed: str | None = None, code: OutpostRefusalCode | None = None
    ) -> None:
        task = asyncio.create_task(self._deliver(call_id, sealed=sealed, code=code, until=until))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def _deliver(
        self, call_id: str, *, sealed: str | None, code: OutpostRefusalCode | None, until: float
    ) -> None:
        """Sends an answer or a refusal until the door has taken it, or the call's deadline has passed:
        a refusal that never arrives would leave a write that was not made in doubt."""
        backoff = Backoff(base=0.5, cap=10.0, rng=self._jitter)
        while True:
            try:
                if sealed is not None:
                    await self.uplink.answer(call_id, sealed)
                elif code is not None:
                    await self.uplink.refuse(call_id, code)
                break
            except DoorRefusedError as refused:
                if refused.code in _SETTLED:
                    break
                if refused.code is OutpostDoorCode.INSTANCE_REVOKED:
                    if self._exit is None:
                        self._exit = Exit.REVOKED
                    return
                if refused.code in _FINAL:
                    log.error("call.not_taken", call_id=call_id, code=refused.problem.code)
                    return
                wait = max(backoff.next(), float(min(refused.problem.retry_after or 0, MAX_RETRY_AFTER_S)))
                reason = refused.problem.code
            except DoorUnreachableError as error:
                wait, reason = backoff.next(), str(error)
            if self._judged_now() + wait >= until or self._stop.is_set():
                log.warning("call.not_delivered", call_id=call_id, reason=reason)
                return
            await self._nap(wait)
        await self._journalled(self.journal.acknowledge(call_id))

    # ───────────────────────── after a restart ─────────────────────────

    async def _recover(self) -> None:
        """Reports what a former process left: a call it began and did not answer is `unanswered`, and
        is not executed again; an answer or a refusal the door never confirmed is sent again."""
        now = self._judged_now()
        unsettled = self.journal.unsettled(now)
        for entry in self.journal.under_way():
            await self._left_in_the_middle(entry, now)
        for entry in unsettled:
            if entry.state == REFUSED and entry.refusal is not None:
                self._send(entry.id, code=OutpostRefusalCode(entry.refusal), until=entry.keep_until)
            elif (sealed := await self.journal.answer(entry.id)) is not None:
                self._send(entry.id, sealed=sealed, until=entry.keep_until)

    async def _left_in_the_middle(self, entry: Entry, now: float) -> None:
        facts = Facts.of(entry)
        if entry.state != BEGUN:
            # Taken and never begun: its server received nothing.
            await self._refuse(entry.id, OutpostRefusalCode.BUSY, facts, entry.keep_until, write=entry.write)
            return
        outcome = OutpostAnswerOutcome.UNANSWERED
        answer = OutpostAnswer(id=entry.id, outcome=outcome, write=entry.write, replayed=True)
        sealed = self._sealed(entry.answer_key, answer)
        await self._journalled(self.journal.finish(entry.id, outcome, sealed=sealed))
        await self._line(
            OutpostAuditEvent.REPLAYED, entry.id, outcome=outcome, write=entry.write, **facts.line()
        )
        log.warning(
            "call.left_unanswered",
            call_id=entry.id,
            write=entry.write,
            detail="a former process began this call and recorded no answer: it is reported "
            "`unanswered` and is not executed again",
        )
        if sealed is not None and now < entry.keep_until:
            self._send(entry.id, sealed=sealed, until=entry.keep_until)


def _lets_through(endpoint: OutpostEndpoint, tool: McpTool) -> bool:
    """Whether a listing shows this tool: one a call could name, and would not be refused for writing."""
    if not 1 <= len(tool.name) <= 128 or not allows(endpoint, tool.name):
        return False
    return endpoint.writes == "allow" or tool.read_only


def _tool(tool: McpTool) -> OutpostTool:
    return OutpostTool(
        name=tool.name,
        description=tool.description[:8192],
        input_schema=tool.input_schema,
        read_only=tool.read_only,
        output_schema=tool.output_schema,
    )
