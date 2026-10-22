# SPDX-License-Identifier: Apache-2.0
"""A step a prompt agent carries (ADR 0080): the runner's own tool loop, no coding agent.

The model is the run's gateway model, called with the run's key, which never leaves this process. The
tools are the run's MCP servers (`choregos`, `memory`), each call judged by the same guard rails as an
ACP permission request, and four of the runner's: `read_file` and `list_files` over the workspace,
read-only; `write_output` for the step's declared deliverables; `submit_result` for the result, which
is validated as an agent's `result.json` is and refused back to the model when it does not hold.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from choregos_adapters.mcp.client import ClientMcp
from choregos_contracts import StageInput, StageResult
from choregos_core.egress import (
    AllowedRule,
    EgressPolicy,
    NetworkBounds,
    host_port_of,
    normalize_host,
    use_policy,
)
from choregos_core.prompt_loop import LoopOutcome, LoopRequest, LoopTool, run_loop

from .budget import gateway_budget_refusal
from .guardrails import GuardRails
from .outputs import OUTPUTS_DIR, expected_files
from .result import load_result
from .workspace import inline_outputs

Record = Callable[[str, dict[str, Any]], Awaitable[None]]

SYSTEM = (
    "You carry one step of a governed workflow. The task follows. Use only the tools offered to you. "
    "When you are done, call `submit_result` once with your status, a one-sentence summary and the "
    "step's outputs; if it is refused, fix what it says and call it again."
)
#: How often a model that answers in text without submitting is reminded to submit.
MAX_REMINDERS = 2
_NAME = re.compile(r"[^a-zA-Z0-9_-]")
#: What a tool answer the model reads can hold.
_MAX_TOOL_TEXT = 20_000
_MAX_FILE_BYTES = 200_000


class ModelCallError(Exception):
    """The model call failed: the gateway refused it, or answered something that is not a completion."""


@dataclass
class PromptStep:
    """What the loop left: its outcome, the result it validated, or why there is none."""

    outcome: LoopOutcome | None = None
    result: StageResult | None = None
    #: Why no result: the last validation error, the model's failure, the timeout.
    error: str | None = None
    #: The reason the runner stops the step with: `max_turns`, `timeout`, `model_error`.
    reason: str | None = None
    #: A gateway budget refusal, word for word (S27-01).
    refused: str | None = None
    denials: int = 0
    tools: list[str] = field(default_factory=list)


@dataclass
class _Mcp:
    client: ClientMcp
    server: str
    tool: str


def tool_name(server: str, tool: str) -> str:
    """`server__tool`, in the alphabet and the length chat completions accepts for a function."""
    return _NAME.sub("_", f"{server}__{tool}")[:64]


def _auth(env: dict[str, str]) -> dict[str, str]:
    """The header a server's `env` declares, as `ClientMcp` takes it: `Authorization: Bearer x` is a
    bearer token, any other header carries its value as it is."""
    for header, value in env.items():
        if header.lower() == "authorization":
            return {"token": value.removeprefix("Bearer ").strip()}
        return {"token": value, "auth_header": header}
    return {}


def step_policy(stage_input: StageInput) -> EgressPolicy:
    """What the runner's own calls of a prompt step may reach: the MCP servers the step was granted
    and its `allow_domains`, never a host of `deny_domains`. The model gateway is the platform's own
    address, not a call the agent chooses, and does not go through the guarded transport."""
    granted = [hp for ref in stage_input.tools.mcp.values() if (hp := host_port_of(ref.url))]
    rules = [AllowedRule(host, (port,)) for host, port in granted]
    rules += [AllowedRule(normalize_host(host)) for host in stage_input.permissions.allow_domains]
    return EgressPolicy(
        rules=tuple(rules),
        mode="enforce",
        bounds=NetworkBounds(deny=tuple(stage_input.permissions.deny_domains)),
    )


class PromptRun:
    """One prompt step: the tools it offers and how it answers them."""

    def __init__(
        self,
        stage_input: StageInput,
        workspace: Path,
        record: Record,
        guards: GuardRails,
        *,
        model_transport: httpx.AsyncBaseTransport | None = None,
        mcp_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.stage_input = stage_input
        self.workspace = workspace.resolve()
        self.record = record
        self.guards = guards
        self.model_transport = model_transport
        self.mcp_transport = mcp_transport
        self.result_path = self.workspace / ".choregos" / "result.json"
        self.submitted: StageResult | None = None
        self.last_error: str | None = None
        self.files = expected_files(stage_input)
        self._mcp: dict[str, _Mcp] = {}

    # ─────────────────────────── the model ───────────────────────────

    async def complete(self, body: dict[str, Any]) -> dict[str, Any]:
        model = self.stage_input.model
        headers = (
            {"Authorization": f"Bearer {self.stage_input.gateway_key}"}
            if self.stage_input.gateway_key
            else {}
        )
        async with httpx.AsyncClient(transport=self.model_transport, timeout=300) as http:
            response = await http.post(
                f"{model.base_url.rstrip('/')}/chat/completions", json=body, headers=headers
            )
        if response.status_code != 200:
            raise ModelCallError(f"the model answered {response.status_code}: {response.text[:300]}")
        try:
            answer = response.json()
        except ValueError as exc:
            raise ModelCallError("the model answered something that is not JSON") from exc
        if not isinstance(answer, dict):
            raise ModelCallError("the model answered something that is not a completion")
        return answer

    # ─────────────────────────── the tools ───────────────────────────

    async def tools(self) -> list[LoopTool]:
        offered = [
            LoopTool(
                "read_file",
                "Read a text file of the workspace, by its path relative to the workspace.",
                {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            ),
            LoopTool(
                "list_files",
                "List the files under a directory of the workspace (default: its root).",
                {"type": "object", "properties": {"path": {"type": "string"}}},
            ),
            LoopTool(
                "submit_result",
                "Submit the step's result. `outputs` holds each output the task declares.",
                {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["done", "blocked", "needs_human", "failed"]},
                        "summary": {"type": "string"},
                        "outputs": {"type": "object"},
                        "questions": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"text": {"type": "string"}}},
                        },
                    },
                    "required": ["status", "summary"],
                },
            ),
        ]
        if self.files:
            offered.append(
                LoopTool(
                    "write_output",
                    f"Write one of the step's declared files ({', '.join(sorted(self.files.values()))}) "
                    "with its text content.",
                    {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": sorted(self.files.values())},
                            "content": {"type": "string"},
                        },
                        "required": ["name", "content"],
                    },
                )
            )
        for server, ref in self.stage_input.tools.mcp.items():
            if not ref.url:
                continue  # a stdio server is a process a coding agent starts; the loop has none
            client = ClientMcp(ref.url, transport=self.mcp_transport, **_auth(ref.env))  # type: ignore[arg-type]
            try:
                listed = await client.list_tools()
            except Exception as exc:  # a server that does not answer leaves the others
                await self.record("prompt.mcp_unavailable", {"server": server, "error": str(exc)[:300]})
                continue
            for tool in listed:
                name = tool_name(server, tool.name)
                if name in self._mcp or name in {t.name for t in offered}:
                    continue
                self._mcp[name] = _Mcp(client, server, tool.name)
                schema = tool.input_schema or {"type": "object", "properties": {}}
                offered.append(LoopTool(name, tool.description[:1024], schema))
        return offered

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "read_file":
                return self._read(str(arguments.get("path") or ""))
            if name == "list_files":
                return self._list(str(arguments.get("path") or "."))
            if name == "write_output":
                return self._write_output(str(arguments.get("name") or ""), arguments.get("content"))
            if name == "submit_result":
                return self._submit(arguments)
            return await self._call_mcp(name, arguments)
        except Exception as exc:  # a tool's failure is the model's to read, not the run's end
            return f"the tool failed: {str(exc)[:500]}"

    async def _call_mcp(self, name: str, arguments: dict[str, Any]) -> str:
        mcp = self._mcp[name]
        decision = self.guards.decide(
            {"toolCall": {"kind": "other", "title": f"{mcp.server}/{mcp.tool}", "rawInput": arguments}}
        )
        await self.record(
            "session/request_permission", {**decision.to_event(), "tool": f"{mcp.server}/{mcp.tool}"}
        )
        if not decision.allowed:
            return f"refused by the run's guard rails: {decision.reason}"
        answer = await mcp.client.call_tool(mcp.tool, arguments)
        texts = [part.get("text", "") for part in answer.get("content") or [] if isinstance(part, dict)]
        text = "\n".join(t for t in texts if t) or json.dumps(answer.get("structuredContent") or {})
        prefix = "the tool reported an error: " if answer.get("isError") else ""
        return (prefix + text)[:_MAX_TOOL_TEXT]

    def _inside(self, relative: str) -> Path:
        target = (self.workspace / relative).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ValueError(f"`{relative}` is outside the workspace")
        return target

    def _read(self, relative: str) -> str:
        target = self._inside(relative)
        if not target.is_file():
            return f"`{relative}` is not a file of the workspace"
        data = target.read_bytes()[:_MAX_FILE_BYTES]
        return data.decode("utf-8", errors="replace")

    def _list(self, relative: str) -> str:
        root = self._inside(relative)
        if not root.is_dir():
            return f"`{relative}` is not a directory of the workspace"
        found = sorted(
            str(path.relative_to(self.workspace))
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(self.workspace).parts
        )
        return "\n".join(found[:500]) + ("\n…" if len(found) > 500 else "") or "(empty)"

    def _write_output(self, filename: str, content: Any) -> str:
        if filename not in self.files.values():
            return f"`{filename}` is not a file this step declares: {', '.join(sorted(self.files.values()))}"
        if not isinstance(content, str):
            return "`content` must be text"
        target = self.workspace / OUTPUTS_DIR / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"`{filename}` written ({len(content.encode())} bytes)"

    def _submit(self, payload: dict[str, Any]) -> str:
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        self.result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        load = load_result(self.result_path, inline_outputs(self.stage_input), list(self.files.values()))
        if load.result is None:
            self.last_error = load.error
            return f"the result is refused: {load.error}"
        self.submitted = load.result
        return "the result is accepted: the step ends here"

    # ─────────────────────────── the end ───────────────────────────

    async def on_final(self, text: str, reminders: list[int]) -> str | None:
        """A model that ends in text: its text is the result when nothing else is asked of it,
        otherwise it is reminded to submit, at most `MAX_REMINDERS` times."""
        if self.stage_input.prompt is not None and self.stage_input.prompt.response_format == "json_object":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                answer = self._submit(payload)
                if self.submitted is not None:
                    return None
                return self._remind(answer, reminders)
        if not inline_outputs(self.stage_input) and not self.files and text.strip():
            self._submit({"status": "done", "summary": text.strip()[:4000]})
            if self.submitted is not None:
                return None
        return self._remind("your answer is not the step's result", reminders)

    def _remind(self, why: str, reminders: list[int]) -> str | None:
        if len(reminders) >= MAX_REMINDERS:
            return None
        reminders.append(1)
        return f"{why}. Call `submit_result` with the step's status, summary and outputs."


async def run_prompt(
    stage_input: StageInput,
    workspace: Path,
    task: str,
    record: Record,
    guards: GuardRails,
    *,
    model_transport: httpx.AsyncBaseTransport | None = None,
    mcp_transport: httpx.AsyncBaseTransport | None = None,
    timeout_s: float,
) -> PromptStep:
    """Runs the loop on `task` and says how it ended."""
    run = PromptRun(
        stage_input, workspace, record, guards, model_transport=model_transport, mcp_transport=mcp_transport
    )
    with use_policy(step_policy(stage_input), origin="prompt"):
        return await _run(run, stage_input, task, record, timeout_s)


async def _run(
    run: PromptRun, stage_input: StageInput, task: str, record: Record, timeout_s: float
) -> PromptStep:
    call = stage_input.prompt
    tools = await run.tools()
    step = PromptStep(tools=[tool.name for tool in tools])
    await record("prompt.started", {"model": stage_input.model.litellm_model, "tools": step.tools})
    reminders: list[int] = []

    async def on_final(text: str) -> str | None:
        return await run.on_final(text, reminders)

    try:
        step.outcome = await asyncio.wait_for(
            run_loop(
                LoopRequest(
                    model=stage_input.model.litellm_model,
                    max_turns=stage_input.budget.max_turns,
                    max_output_tokens=call.max_output_tokens if call else None,
                    tool_choice=call.tool_choice if call else "auto",
                    response_format=call.response_format if call else "text",
                ),
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}],
                tools,
                run.complete,
                run.call_tool,
                on_event=record,
                on_final=on_final,
                stop_when=lambda: run.submitted is not None,
            ),
            timeout=timeout_s,
        )
    except TimeoutError:
        step.reason, step.error = "timeout", f"the prompt step ran past {timeout_s:.0f} s"
    except (ModelCallError, httpx.HTTPError) as exc:
        step.refused = gateway_budget_refusal([str(exc)])
        step.reason, step.error = "model_error", str(exc)[:500]
    step.result = run.submitted
    step.denials = run.guards.denials
    if step.result is None and step.error is None:
        if step.outcome is not None and step.outcome.status == "max_turns":
            step.reason = "max_turns"
            step.error = f"stopped by the runner: {stage_input.budget.max_turns} model calls without a result"
        else:
            step.error = run.last_error or "the model ended without submitting a result"
    return step
