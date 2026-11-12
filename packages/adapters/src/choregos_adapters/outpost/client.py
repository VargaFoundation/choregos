# SPDX-License-Identifier: Apache-2.0
"""The tool client of a connector reached through an outpost (ADR 0149 §3).

It has the two methods the broker, the governed-write effect and discovery call on a connector's
client, `list_tools` and `call_tool`, and nothing of an MCP connection behind them: a call is
placed at a rendezvous (the port of `port.py`), awaited, read, then forgotten. It is not an MCP
client and extends none: the platform opens no connection to an outpost's server, the outpost does.

What comes back is what a direct connector would have given: the server's result untouched, its
tools as the MCP client describes them, a JSON-RPC error as the `McpError` the MCP client raises.
Everything else is a typed refusal (`refusals.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any, Final

from choregos_contracts import OutpostAnswer, OutpostAnswerOutcome, OutpostCallKind, OutpostCallState

from ..mcp import McpError, McpTool
from . import port
from .refusals import (
    OutpostInDoubt,
    OutpostNotAdmitted,
    OutpostOffline,
    OutpostPaused,
    OutpostRefusal,
    OutpostRefused,
    OutpostTimeout,
)

#: What a connector that says nothing waits: the contract's default (`McpOutpostConnectorConfig`).
DEFAULT_TIMEOUT_S: Final = 30.0
#: What the agent reads when the outpost's server answered more than the outpost may send back.
OVERSIZED: Final = (
    "the call was made at the outpost `{outpost}` and its result is larger than the outpost sends "
    "back: nothing of it was returned; ask for less"
)


#: The cancellations under way: the event loop keeps only a weak reference to a task, and one that
#: nobody holds may be collected before it has run.
_GIVING_UP: set[asyncio.Future[None]] = set()


def _given_up(cancelling: asyncio.Future[None]) -> None:
    _GIVING_UP.discard(cancelling)
    if not cancelling.cancelled():
        cancelling.exception()  # read, so that a failure is not reported as never retrieved


class OutpostToolClient:
    """The tools of one endpoint alias of one outpost, called through a rendezvous."""

    def __init__(
        self,
        rendezvous: port.Rendezvous,
        *,
        endpoint: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        asserted: Mapping[str, Any] | None = None,
        write_key: str | None = None,
    ) -> None:
        self._rendezvous = rendezvous
        self._endpoint = endpoint
        self._timeout_s = float(timeout_s)
        self._asserted = dict(asserted or {})
        self._write_key = write_key

    async def list_tools(self) -> list[McpTool]:
        """The tools the alias's server lists and the outpost's policy lets through (discovery)."""
        call = port.Call(
            kind=OutpostCallKind.LIST_TOOLS,
            endpoint=self._endpoint,
            timeout_s=self._timeout_s,
            asserted=self._asserted,
        )
        answer = await self._exchange(call)
        if answer.outcome is OutpostAnswerOutcome.RESULT and answer.tools is not None:
            return [
                McpTool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema),
                    read_only=tool.read_only,
                    output_schema=tool.output_schema,
                )
                for tool in answer.tools
            ]
        raise self._unreadable(answer, "a tool listing")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """`tools/call` at the outpost's server: its result, as it answered it."""
        call = port.Call(
            kind=OutpostCallKind.CALL_TOOL,
            endpoint=self._endpoint,
            timeout_s=self._timeout_s,
            tool=name,
            arguments=dict(arguments),
            asserted=self._asserted,
            write_key=self._write_key,
        )
        answer = await self._exchange(call)
        if answer.outcome is OutpostAnswerOutcome.RESULT and answer.result is not None:
            return dict(answer.result)
        if answer.outcome is OutpostAnswerOutcome.OVERSIZED:
            # The call was made: it is an answer, not a refusal, and it is booked like one.
            text = OVERSIZED.format(outpost=self._rendezvous.outpost)
            return {"content": [{"type": "text", "text": text}], "isError": True}
        raise self._unreadable(answer, "a tool's result")

    async def _exchange(self, call: port.Call) -> OutpostAnswer:
        """Place the call, wait for what becomes of it, and return its answer; anything else is a
        typed refusal. A call that may be asked again is forgotten once it was read; a write is left
        for whoever makes its result durable (ADR 0148 §11)."""
        try:
            call_id = await self._rendezvous.place(call)
        except port.NotAdmitted as refused:
            raise self._not_admitted(refused) from refused
        try:
            outcome = await self._rendezvous.wait(call_id)
        except BaseException:
            # Whoever waited went away (a request cut short, a task cancelled): the platform says it
            # stops waiting, so that the instance that holds the call learns it at its next poll.
            await self._give_up(call_id)
            raise
        write = outcome.write if outcome is not None else call.write_key is not None
        if not write:
            await self._rendezvous.forget(call_id)
        return self._answer_of(call, call_id, outcome, write)

    async def _give_up(self, call_id: str) -> None:
        """Cancel the call, whatever becomes of the task that gives up. It runs as a task of its own:
        a request that was cancelled is cancelled again at its next await, and the cancellation of a
        call must still reach the rendezvous. A failure here changes nothing: the call's own time
        closes it."""
        cancelling = asyncio.ensure_future(self._rendezvous.cancel(call_id))
        _GIVING_UP.add(cancelling)
        cancelling.add_done_callback(_given_up)
        with contextlib.suppress(Exception):
            await asyncio.shield(cancelling)

    def _answer_of(
        self, call: port.Call, call_id: str, outcome: port.Outcome | None, write: bool
    ) -> OutpostAnswer:
        outpost, endpoint = self._rendezvous.outpost, call.endpoint
        state = outcome.state if outcome is not None else None
        if (
            outcome is not None
            and state is OutpostCallState.ANSWERED
            and outcome.answer is not None
            and outcome.answer.outcome is not OutpostAnswerOutcome.UNANSWERED
        ):
            return outcome.answer
        if outcome is not None and state is OutpostCallState.REFUSED and outcome.refusal is not None:
            raise OutpostRefused(
                f"the outpost `{outpost}` refused the call to `{endpoint}` ({outcome.refusal.value}): "
                "its own policy decides what it makes, and nothing reached its server",
                outpost=outpost,
                endpoint=endpoint,
                refusal=outcome.refusal,
            )
        if state is OutpostCallState.EXPIRED:
            raise OutpostOffline(
                f"the outpost `{outpost}` is offline: no instance of it claimed the call to `{endpoint}` "
                f"within {call.timeout_s:g} seconds, and nothing reached its server",
                outpost=outpost,
                endpoint=endpoint,
            )
        # A claim and no answer: a lease that ended, an answer that says `unanswered` or does not
        # open, an answer the rendezvous no longer holds. Executed or not, nobody knows.
        if write:
            raise OutpostInDoubt(
                f"the outpost `{outpost}` claimed the write to `{endpoint}` and did not answer in time: "
                "nobody knows whether its server executed it, and it is never asked again",
                outpost=outpost,
                endpoint=endpoint,
                call_id=call_id,
                tool=call.tool,
                instance_id=outcome.instance_id if outcome is not None else None,
                claimed_at=outcome.claimed_at if outcome is not None else None,
            )
        raise OutpostTimeout(
            f"the outpost `{outpost}` claimed the call to `{endpoint}` and did not answer in time: "
            "it may be asked again",
            outpost=outpost,
            endpoint=endpoint,
        )

    def _not_admitted(self, refused: port.NotAdmitted) -> OutpostRefusal:
        """The refusal of a call the rendezvous did not take. It names the outpost, whatever the
        rendezvous said: a refusal is read by an agent, or by a person, who has to know where."""
        outpost, endpoint = self._rendezvous.outpost, self._endpoint
        said = str(refused)
        if f"`{outpost}`" not in said:
            said = f"the call to `{endpoint}` was not handed to the outpost `{outpost}`: {said}"
        if refused.reason in port.CLOSED:
            return OutpostPaused(said, outpost=outpost, endpoint=endpoint)
        if refused.reason == port.OFFLINE:
            return OutpostOffline(said, outpost=outpost, endpoint=endpoint)
        return OutpostNotAdmitted(said, outpost=outpost, endpoint=endpoint, reason=refused.reason)

    def _unreadable(self, answer: OutpostAnswer, expected: str) -> McpError:
        """The outpost's server answered a JSON-RPC error, or something that is not what was asked:
        what the MCP client raises for a server that answers an error or nothing readable."""
        if answer.outcome is OutpostAnswerOutcome.ERROR and answer.error is not None:
            return McpError(answer.error.message, answer.error.code)
        return McpError(
            f"the outpost `{self._rendezvous.outpost}` answered `{answer.outcome.value}` where {expected} "
            "was expected"
        )


__all__ = ["DEFAULT_TIMEOUT_S", "OutpostToolClient"]
