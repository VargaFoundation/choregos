# SPDX-License-Identifier: Apache-2.0
"""Headless ACP client: starts the agent, holds the session, arbitrates permissions."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import agent_base_environment
from .guard import TurnGuard
from .protocol import (
    ACP_VERSION,
    FS_READ_TEXT_FILE,
    FS_WRITE_TEXT_FILE,
    INITIALIZE,
    PERMISSION_DENIED,
    PROTOCOL_VERSION,
    SESSION_CANCEL,
    SESSION_LOAD,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_REQUEST_PERMISSION,
    SESSION_UPDATE,
    Notification,
    Request,
    Response,
    decode,
    error_response,
)

#: How many of the agent's last text chunks a `PromptOutcome` keeps.
SAID_KEPT = 20

#: The longest ACP message (one JSON line on the agent's stdout) the runner reads (#498).
#: asyncio's default is 64 KiB; one `tool_call_update` carrying a file the agent read (a
#: transcript, a log) is longer, and the read loop died on it: the agent's answer was then
#: never read, and the run waited its whole budget before exiting 1.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024

PermissionHandler = Callable[[dict[str, Any]], Awaitable[tuple[bool, str]]]
UpdateHandler = Callable[[dict[str, Any]], Awaitable[None]]


class AgentUnreachableError(RuntimeError):
    """The backend did not answer `initialize`: the backend is broken, not the agent."""


class AgentProtocolError(RuntimeError):
    """The agent broke the ACP protocol."""


@dataclass
class PromptOutcome:
    stop_reason: str = "end_turn"
    turns: int = 0
    #: Text fragments received from the agent. Zero messages AND zero tools = the model did not
    #: answer: this is not a distracted agent, it is an access that does not work.
    messages: int = 0
    tool_calls: int = 0
    permission_denials: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)
    #: The last text chunks the agent said (bounded): a gateway refusal relayed as text is read
    #: there (S27-01).
    said: list[str] = field(default_factory=list)
    #: Why the runner stopped the agent itself (S27-07): `max_turns` or `loop`; `None` otherwise.
    guard: str | None = None
    guard_detail: str = ""


def _option_id(params: dict[str, Any], allowed: bool) -> str:
    """The option id to SEND BACK, chosen among those the agent offers.

    ACP lets the agent name its options: it announces `{kind, optionId, name}`, and the
    client answers with an `optionId` FROM THIS LIST. We used to send back `allow_once`, a name
    that claude-code-acp does not offer: it offers `allow` (of kind `allow_once`). Our "yes"
    therefore matched nothing, the agent read it as a refusal, and it wrote in its transcript
    that its writes were "refused by the user" while the platform's journal said "allowed".
    Two versions of the same step, and neither was lying.
    """
    # Order matters: `once` first. A guardrail decision is about THIS call; answering "always"
    # would give the agent a blank cheque for all the following ones, which nobody granted.
    wanted = ("allow_once", "allow_always") if allowed else ("reject_once", "reject_always")
    options = [o for o in (params.get("options") or []) if isinstance(o, dict)]
    for kind in wanted:
        for option in options:
            if option.get("kind") == kind and option.get("optionId"):
                return str(option["optionId"])
    # No option of the expected kind: fall back on the kind's canonical name, which is
    # also the id used by agents that rename nothing.
    return wanted[0]


class AcpClient:
    """ACP client over stdio. No dependency on the backend: the contract is what counts."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        on_update: UpdateHandler | None = None,
        on_permission: PermissionHandler | None = None,
        read_timeout: float = 120.0,
        guard: TurnGuard | None = None,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        self.command = command
        self.cwd = cwd
        # The runner's environment without the run's credentials (ADR 0115): under `k8s_job`, ACA,
        # local Docker and Tekton, the run token is in the runner's environment, never the agent's.
        self.env = {**agent_base_environment(), **(env or {})}
        self.on_update = on_update
        self.on_permission = on_permission
        self.read_timeout = read_timeout
        self.max_message_bytes = max_message_bytes
        #: Messages longer than `max_message_bytes`, dropped unread (#498): how many.
        self.dropped_messages = 0
        #: Why the read loop stopped, when it died of an error rather than of the agent's EOF.
        self.reader_error: str | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None
        self.capabilities: dict[str, Any] = {}
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Response]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self.stderr_tail: list[str] = []
        self.outcome = PromptOutcome()
        self.guard = guard or TurnGuard()
        #: Set when the guard trips: the prompt in flight stops waiting for the agent.
        self._tripped = asyncio.Event()
        #: How long an agent stopped by the guard gets to end its turn before it is left behind.
        self.cancel_grace_s = 10.0

    # ───────────────────────── cycle de vie ─────────────────────────

    async def start(self) -> None:
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=self.cwd,
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=self.max_message_bytes,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise AgentUnreachableError(f"cannot start {self.command[0]}: {exc}") from exc
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def stop(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                # A reader that died of an error already said so (`reader_error`, the pending
                # requests failed with it): raising it again here replaced the run's readable
                # error with a bare traceback and exit 1 (#498).
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except TimeoutError:
                self.process.kill()

    async def __aenter__(self) -> AcpClient:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ───────────────────────── protocole ─────────────────────────

    async def initialize(self, client_capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.request(
            INITIALIZE,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "acpVersion": ACP_VERSION,
                # We announce what we SERVE, and we serve both file methods (further down).
                # The two possible lies are costly, and we paid for both:
                # announcing `fs` then refusing the call sends the agent off to redo everything with `bash`
                # ("the file system is not exposed", says its transcript);
                # announcing `false` makes it disable its edit tools, and it can no longer
                # write anything at all.
                "clientCapabilities": client_capabilities
                or {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": False},
                "clientInfo": {"name": "choregos-runner", "version": "1.0.0"},
            },
            timeout=60.0,
        )
        if response.failed:
            raise AgentUnreachableError(f"initialize refused: {response.error}")
        self.capabilities = (response.result or {}).get("agentCapabilities", {})
        return dict(response.result or {})

    async def new_session(self, cwd: str, mcp_servers: list[dict[str, Any]] | None = None) -> str:
        response = await self.request(
            SESSION_NEW, {"cwd": cwd, "mcpServers": mcp_servers or []}, timeout=120.0
        )
        if response.failed:
            raise AgentProtocolError(f"session/new refused: {response.error}")
        session_id = (response.result or {}).get("sessionId")
        if not session_id:
            raise AgentProtocolError("session/new returned no sessionId")
        self.session_id = str(session_id)
        return self.session_id

    @property
    def can_load_session(self) -> bool:
        """The agent announced `loadSession` at `initialize`: it can resume a conversation."""
        return bool(self.capabilities.get("loadSession"))

    async def load_session(
        self, session_id: str, cwd: str, mcp_servers: list[dict[str, Any]] | None = None
    ) -> str:
        """Reopens a conversation the agent kept (ACP `session/load`): it replays it as
        `session/update` notifications, then the next prompt continues it."""
        response = await self.request(
            SESSION_LOAD,
            {"sessionId": session_id, "cwd": cwd, "mcpServers": mcp_servers or []},
            timeout=120.0,
        )
        if response.failed:
            raise AgentProtocolError(f"session/load refused: {response.error}")
        self.session_id = session_id
        return session_id

    async def prompt(self, text: str, *, timeout: float) -> PromptOutcome:
        """Send a prompt turn and let the agent work until `stopReason`."""
        if self.session_id is None:
            raise AgentProtocolError("no open session")
        if self.outcome.guard is not None:
            # Stopped by the guard: no further prompt (a repair, a DoD round) reopens the session.
            return self.outcome
        self.outcome.turns += 1
        try:
            response = await self._prompt_until_tripped(text, timeout)
            if response is None:
                return self.outcome
        except TimeoutError:
            await self.cancel()
            self.outcome.stop_reason = "timeout"
            self.outcome.cancelled = True
            return self.outcome
        if response.failed:
            self.outcome.errors.append(str(response.error))
            self.outcome.stop_reason = "error"
            return self.outcome
        self.outcome.stop_reason = str((response.result or {}).get("stopReason", "end_turn"))
        return self.outcome

    async def _prompt_until_tripped(self, text: str, timeout: float) -> Response | None:
        """The prompt's response — or `None` when the guard stopped the agent first."""
        send_task = asyncio.create_task(
            self.request(
                SESSION_PROMPT,
                {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
                timeout=timeout,
            )
        )
        alarm = asyncio.create_task(self._tripped.wait())
        done, _ = await asyncio.wait({send_task, alarm}, return_when=asyncio.FIRST_COMPLETED)
        if send_task in done:
            alarm.cancel()
            return send_task.result()
        # The guard tripped: the session was cancelled; the agent gets a moment to end its turn.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(send_task), timeout=self.cancel_grace_s)
        if not send_task.done():
            send_task.cancel()
            with contextlib.suppress(BaseException):
                await send_task
        self.outcome.stop_reason = str(self.outcome.guard)
        self.outcome.cancelled = True
        return None

    async def _watch(self, tool_call: Any) -> bool:
        """Counts a tool call against the guard; `True` when the agent must stop."""
        if not isinstance(tool_call, dict):
            return self.outcome.guard is not None
        reason = self.guard.observe(tool_call)
        if reason is not None and self.outcome.guard is None:
            self.outcome.guard, self.outcome.guard_detail = reason, self.guard.detail
            self._tripped.set()
            await self.cancel()
        return reason is not None

    async def cancel(self) -> None:
        if self.session_id is None or self.process is None:
            return
        with contextlib.suppress(Exception):
            await self.notify(SESSION_CANCEL, {"sessionId": self.session_id})
        self.outcome.cancelled = True

    async def request(self, method: str, params: dict[str, Any], *, timeout: float = 60.0) -> Response:
        if self.process is None or self.process.stdin is None:
            raise AgentUnreachableError("agent process missing")
        if self.reader_error is not None:
            raise AgentUnreachableError(f"the runner stopped reading the agent: {self.reader_error}")
        self._next_id += 1
        request = Request(id=self._next_id, method=method, params=params)
        future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        self._pending[request.id] = future
        self.process.stdin.write(request.encode())
        await self.process.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise AgentUnreachableError(self._why_silent(method, timeout)) from exc
        finally:
            self._pending.pop(request.id, None)

    def _why_silent(self, method: str, timeout: float) -> str:
        """What we know about an agent that did not answer, instead of a bare `TimeoutError`.

        On 2026-09-27, on the dev tenant, `opencode` did not answer `initialize` and the
        pod's journal carried ONLY the `asyncio.wait_for` stack. Nothing about the command run,
        nothing about the process still being alive, nothing from its error output, although
        `stderr_tail` has collected it since day one and nobody read it at that place. A
        silent agent is the most frequent case of a first deployment: that is exactly where
        a sentence that says what to look at is needed.
        """
        morceaux = [
            f"the agent did not answer `{method}` within {timeout:.0f} s",
            f"command: {' '.join(self.command)}",
        ]
        if self.process is None:
            morceaux.append("process: missing")
        elif self.process.returncode is None:
            morceaux.append("process: alive (so it did not crash; it wrote nothing)")
        else:
            morceaux.append(f"process: exited, code {self.process.returncode}")
        if self.reader_error:
            morceaux.append(f"the runner stopped reading the agent: {self.reader_error}")
        if self.dropped_messages:
            morceaux.append(
                f"{self.dropped_messages} message(s) longer than {self.max_message_bytes} bytes dropped"
            )
        if self.stderr_tail:
            last_lines = " / ".join(
                line_text.strip() for line_text in self.stderr_tail[-5:] if line_text.strip()
            )
            morceaux.append(f"agent's error output: {last_lines[:600]}")
        else:
            morceaux.append("agent's error output: EMPTY")
        return " — ".join(morceaux)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            return
        self.process.stdin.write(Notification(method=method, params=params).encode())
        await self.process.stdin.drain()

    async def respond(self, response: Response) -> None:
        if self.process is None or self.process.stdin is None:
            return
        self.process.stdin.write(response.encode())
        await self.process.stdin.drain()

    # ───────────────────────── boucle de lecture ─────────────────────────

    async def _read_loop(self) -> None:
        try:
            await self._read_messages()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Nobody reads the agent any more: every request in flight would wait out its whole
            # timeout for an answer that can no longer arrive. They fail now, saying why.
            self.reader_error = f"{type(exc).__name__}: {exc}"[:500]
            command = " ".join(self.command)
            reason = f"the runner stopped reading the agent: {self.reader_error} — command: {command}"
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(AgentUnreachableError(reason))
            raise

    async def _read_messages(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            try:
                line = await self.process.stdout.readline()
            except ValueError:
                # Longer than `max_message_bytes`: asyncio has already discarded it, and the
                # stream reads on from the next line. One update lost beats a deaf runner.
                self.dropped_messages += 1
                continue
            if not line:
                break
            message = decode(line)
            if message is None:
                continue
            if isinstance(message, Response):
                future = self._pending.get(message.id)
                if future is not None and not future.done():
                    future.set_result(message)
                continue
            if isinstance(message, Notification):
                await self._handle_notification(message)
                continue
            await self._handle_request(message)

    async def _handle_notification(self, notification: Notification) -> None:
        if notification.method == SESSION_UPDATE:
            await self._on_update(notification.params)

    async def _handle_request(self, request: Request) -> None:
        if request.method == SESSION_REQUEST_PERMISSION:
            if await self._watch((request.params or {}).get("toolCall")):
                # Stopped by the guard: nothing more is allowed, whatever the policy says.
                await self.respond(
                    Response(
                        id=request.id,
                        result={
                            "outcome": {"outcome": "selected", "optionId": _option_id(request.params, False)},
                            "reason": f"stopped by the runner: {self.outcome.guard}",
                        },
                    )
                )
                self.outcome.permission_denials += 1
                return
            allowed, reason = await self._decide(request.params)
            choice = _option_id(request.params, allowed)
            if not allowed:
                self.outcome.permission_denials += 1
                await self.respond(
                    Response(
                        id=request.id,
                        result={"outcome": {"outcome": "selected", "optionId": choice}, "reason": reason},
                    )
                )
                return
            await self.respond(
                Response(id=request.id, result={"outcome": {"outcome": "selected", "optionId": choice}})
            )
            return
        if request.method == SESSION_UPDATE:
            await self._on_update(request.params)
            await self.respond(Response(id=request.id, result={}))
            return
        if request.method == FS_READ_TEXT_FILE:
            await self._read_text_file(request)
            return
        if request.method == FS_WRITE_TEXT_FILE:
            await self._write_text_file(request)
            return
        await self.respond(
            error_response(request.id, PERMISSION_DENIED, f"unhandled method: {request.method}")
        )

    def _resolve(self, raw: Any) -> Path | None:
        """A path inside the workspace, or nothing.

        The workspace is the boundary: a relative path is attached to it, an absolute path
        must fall inside it. What goes outside, such as `../../etc/passwd` or a link that climbs
        out, is not an agent error to correct, it is a request to refuse.
        """
        if not isinstance(raw, str) or not raw:
            return None
        root = Path(self.cwd).resolve()
        target = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        return target if target == root or root in target.parents else None

    async def _read_text_file(self, request: Request) -> None:
        params = request.params or {}
        target = self._resolve(params.get("path"))
        if target is None:
            await self.respond(error_response(request.id, PERMISSION_DENIED, "path outside the workspace"))
            return
        try:
            lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError as exc:
            await self.respond(error_response(request.id, PERMISSION_DENIED, f"cannot read: {exc}"))
            return
        # `line` is 1-indexed in ACP; `limit` bounds the number of lines returned.
        start = max(int(params.get("line") or 1) - 1, 0)
        limit_value = params.get("limit")
        end = start + int(limit_value) if limit_value else len(lines)
        await self.respond(Response(id=request.id, result={"content": "".join(lines[start:end])}))

    async def _write_text_file(self, request: Request) -> None:
        params = request.params or {}
        target = self._resolve(params.get("path"))
        if target is None:
            await self.respond(error_response(request.id, PERMISSION_DENIED, "path outside the workspace"))
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(params.get("content", "")), encoding="utf-8")
        except OSError as exc:
            await self.respond(error_response(request.id, PERMISSION_DENIED, f"cannot write: {exc}"))
            return
        await self.respond(Response(id=request.id, result={}))

    async def _on_update(self, params: dict[str, Any]) -> None:
        update = params.get("update", params)
        kind = str(update.get("sessionUpdate", update.get("type", "")))
        if kind in {"tool_call", "tool_call_update"} and update.get("status") != "in_progress":
            self.outcome.tool_calls += 1
        if kind in {"agent_message_chunk", "agent_thought_chunk"}:
            self.outcome.messages += 1
            content = update.get("content")
            text = content.get("text") if isinstance(content, dict) else None
            if isinstance(text, str) and text:
                self.outcome.said.append(text[:500])
                del self.outcome.said[:-SAID_KEPT]
        if kind in {"tool_call", "tool_call_update"}:
            # The update too: it is often the first to carry the call's arguments (S22-44).
            await self._watch(update)
        if self.on_update is not None:
            await self.on_update(params)

    async def _decide(self, params: dict[str, Any]) -> tuple[bool, str]:
        if self.on_permission is None:
            return True, "no permission policy: allowed"
        return await self.on_permission(params)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            self.stderr_tail.append(text)
            if len(self.stderr_tail) > 200:
                self.stderr_tail.pop(0)
