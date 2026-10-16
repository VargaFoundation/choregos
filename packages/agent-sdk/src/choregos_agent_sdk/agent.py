# SPDX-License-Identifier: Apache-2.0
"""`@agent`: one async function becomes an ACP agent the runner can start.

```python
from choregos_agent_sdk import agent, Context, Result

@agent(name="ticket-triager")
async def run(ctx: Context) -> Result:
    ...
    return Result(summary="triaged", facts={"priority": "P2"})

if __name__ == "__main__":
    run.serve()
```
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from acp import PROTOCOL_VERSION, PromptResponse, RequestError, run_agent
from acp.schema import (
    AgentCapabilities,
    HttpMcpServer,
    Implementation,
    InitializeResponse,
    McpCapabilities,
    McpServerStdio,
    NewSessionResponse,
    PromptCapabilities,
    SseMcpServer,
    TextContentBlock,
)

from ._version import __version__
from .context import Context, env_int
from .errors import MaxTurnsExceeded, PermissionDenied, sdk_error_in
from .result import Result, failure, write_result
from .session import RunSession

AgentFunction = Callable[[Context], Awaitable[Result]]


@dataclass
class Agent:
    """A function and its name; `serve()` runs it as an ACP agent on stdio."""

    function: AgentFunction
    name: str

    async def __call__(self, ctx: Context) -> Result:
        return await self.function(ctx)

    def serve(self) -> None:
        """Runs until the runner closes the connection. Standard output is the protocol's: from
        here on `print` goes to standard error, where the runner's log picks it up."""
        asyncio.run(_serve(self))


def agent(*, name: str) -> Callable[[AgentFunction], Agent]:
    """Declares the agent. `name` shows in the runner's journal."""

    def declare(function: AgentFunction) -> Agent:
        return Agent(function=function, name=name)

    return declare


class _Server:
    """The ACP side: initialize, open a session, run the function on each prompt."""

    def __init__(self, declared: Agent, env: dict[str, str] | None = None) -> None:
        self.declared = declared
        self.env = dict(os.environ) if env is None else env
        self._conn: Any = None
        self._sessions: dict[str, RunSession] = {}
        self._turns: dict[str, int] = {}
        self._tasks: dict[str, asyncio.Task[Result]] = {}

    def on_connect(self, conn: Any) -> None:
        self._conn = conn

    async def initialize(self, protocol_version: int, **_: Any) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=min(protocol_version, PROTOCOL_VERSION),
            agent_capabilities=AgentCapabilities(
                # A code agent replays from scratch: there is no conversation to resume (v1).
                load_session=False,
                mcp_capabilities=McpCapabilities(http=True, sse=False),
                prompt_capabilities=PromptCapabilities(),
            ),
            agent_info=Implementation(name=self.declared.name, version=__version__),
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **_: Any,
    ) -> NewSessionResponse:
        session_id = f"sdk-{uuid4().hex[:12]}"
        self._sessions[session_id] = RunSession(
            conn=self._conn,
            session_id=session_id,
            workspace=Path(cwd),
            mcp_servers=list(mcp_servers or []),
            max_turns=env_int("CHOREGOS_MAX_TURNS", self.env),
        )
        self._turns[session_id] = 0
        return NewSessionResponse(session_id=session_id)

    async def prompt(self, session_id: str, prompt: list[Any], **_: Any) -> PromptResponse:
        session = self._sessions.get(session_id)
        if session is None:
            raise RequestError.invalid_params({"sessionId": "unknown session"})
        text = "\n".join(block.text for block in prompt if isinstance(block, TextContentBlock))
        self._turns[session_id] += 1
        task = asyncio.create_task(self._run(session, text, self._turns[session_id]))
        self._tasks[session_id] = task
        await asyncio.wait({task})
        self._tasks.pop(session_id, None)
        return PromptResponse(stop_reason="cancelled" if task.cancelled() else "end_turn")

    async def cancel(self, session_id: str, **_: Any) -> None:
        task = self._tasks.get(session_id)
        if task is not None:
            task.cancel()

    async def _run(self, session: RunSession, prompt: str, turn: int) -> Result:
        """The function, then its result on disk. The step always ends with a valid result file:
        what the code raised becomes a `failed` result that says why."""
        ctx = Context(session, prompt, turn, self.env)
        try:
            outcome = await self.declared.function(ctx)
            if not isinstance(outcome, Result):
                outcome = failure(f"the agent returned {type(outcome).__name__}, not a Result", "agent_error")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A framework's client wraps what its transport raises: look for the SDK's own error.
            limit = sdk_error_in(exc)
            if isinstance(limit, PermissionDenied):
                outcome = failure(f"the runner's guard refused an action: {limit}", "permission_denied")
            elif isinstance(limit, MaxTurnsExceeded):
                outcome = failure(f"the agent reached the step's turn limit: {limit}", "max_turns")
            else:
                traceback.print_exc(file=sys.stderr)
                outcome = failure(f"the agent failed: {type(exc).__name__}: {exc}", "agent_error")
        finally:
            await ctx.aclose()
        try:
            write_result(session.workspace, outcome)
        except Exception as exc:  # a result the contract refuses (e.g. a fact that is not a plain value)
            write_result(session.workspace, failure(f"the agent's result is invalid: {exc}", "agent_error"))
        return outcome


async def _serve(declared: Agent) -> None:
    """Gives the protocol the real standard output and points `sys.stdout` at standard error."""
    loop = asyncio.get_running_loop()
    protocol_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)  # a print reaches the log at once
    reader = asyncio.StreamReader(limit=50 * 1024 * 1024)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    transport, protocol = await loop.connect_write_pipe(_FlowControl, protocol_out)
    writer = asyncio.StreamWriter(transport, protocol, None, loop)
    # `run_agent` names its streams from the client's side: `input_stream` is what the agent WRITES to.
    await run_agent(_Server(declared), writer, reader)  # type: ignore[arg-type]


class _FlowControl(asyncio.BaseProtocol):
    """What `StreamWriter.drain()` needs from a write pipe's protocol."""

    def __init__(self) -> None:
        self._paused = False
        self._waiter: asyncio.Future[None] | None = None

    def pause_writing(self) -> None:
        self._paused = True
        if self._waiter is None:
            self._waiter = asyncio.get_running_loop().create_future()

    def resume_writing(self) -> None:
        self._paused = False
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)
        self._waiter = None

    async def _drain_helper(self) -> None:
        if self._paused and self._waiter is not None:
            await self._waiter
