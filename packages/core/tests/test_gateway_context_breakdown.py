"""What fills an ACP CLI's context, measured by the gateway's callback (ADR 0141 §4, S64-08b).

The runner registers digests of the paragraphs it wrote; the callback splits each call's request as
`context_breakdown` splits a foreign one — its copy is kept equal to the original here —, so a
registered paragraph counts in its section, a `server__tool` of the run's MCP servers in `tools.mcp`,
and everything else in `backend`, scaled to the gateway's `prompt_tokens`. A request with nothing
recognised is all `backend`. The callback never raises (an unreachable API, a malformed call), never
posts text or a digest, fetches a run's parts once, and keeps the first call, the largest of each
window and the last. The request is synthetic, shaped as Claude Code sends it; no real transcript.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import random
import re
import sys
from typing import Any

import pytest
from choregos_core import context_breakdown as core
from choregos_core import llm_spans
from choregos_core.llm_spans import ChoregosLlmSpans, ContextMeasure, HttpContextApi, Registry

RUN_ID = "PROJ-7-implement-1"
#: Words the person wrote in the ticket: they reach the model, never a breakdown.
CANARY = "canary-90b1-the-ticket-said-this"
PLAYBOOK = (
    "You implement one step of the workflow. Read the task, change the code it names, run the tests,\n"
    "then write `.choregos/result.json` with the step's status and a summary of what you changed."
)
TASK = (
    "# Task\n\n"
    f"The order total ignores the discount lines: make `total` subtract them. {CANARY}\n\n"
    "Acceptance: every test in `tests/test_orders.py` passes, and no other file than `src/orders.py` changes."
)
CLAUDE_MD = (
    "# Instructions (Choregos)\n\n"
    "This repository follows `AGENTS.md`. In addition, for this step:\n\n"
    "- Work item: PROJ-7 — Discounts\n- Role: implement\n"
    "- Allowed paths: see `.choregos/allowed_paths.txt`.\n"
    "- Finish by writing `.choregos/result.json`."
)
SKILL = (
    "---\nname: pricing\ndescription: The pricing rules.\n---\n\n"
    "# Pricing\n\n"
    "A discount line carries a negative amount; never round before the total, round the total once."
)
#: What Claude Code adds of its own: none of it is registered.
CLI_SYSTEM = (
    "You are Claude Code, Anthropic's official CLI for Claude. You are an interactive agent that helps "
    "users with software engineering tasks. " * 12
)


def _tool(name: str, description: str) -> dict[str, Any]:
    return {"name": name, "description": description, "input_schema": {"type": "object", "properties": {}}}


def claude_code_request() -> dict[str, Any]:
    """A request shaped as Claude Code sends LiteLLM's `/v1/messages`: its own system prompt and tools,
    `CLAUDE.md` in a reminder, the runner's first prompt, a skill read through a tool."""
    reminder = (
        "<system-reminder>\nAs you answer the user's questions, you can use the following context:\n"
        "# claudeMd\nContents of /workspace/CLAUDE.md (project instructions, checked into the codebase):\n\n"
        f"{CLAUDE_MD}\n\n</system-reminder>"
    )
    return {
        "system": [{"type": "text", "text": CLI_SYSTEM}],
        "tools": [
            _tool("Bash", "Executes a given bash command in a persistent shell session. " * 20),
            _tool("Read", "Reads a file from the local filesystem. " * 15),
            _tool("mcp__crm__lookup", "Look up a customer in the CRM. " * 10),
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": reminder},
                    {"type": "text", "text": f"{PLAYBOOK}\n\n---\n\n{TASK}"},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"file_path": ".claude/skills/pricing/SKILL.md"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": SKILL}],
            },
        ],
    }


def registration() -> dict[str, Any]:
    """What the runner registers (`context.parts`) and the API serves back to the callback."""
    parts = core.register(
        {"system": [PLAYBOOK, CLAUDE_MD], "messages": [TASK], "skills": [SKILL], "knowledge": [""]}
    )
    return {"parts": parts, "mcp_servers": ["crm", "memory"]}


def call(
    request: dict[str, Any] | None = None, *, prompt_tokens: Any = 30_000, backend: str = "claude-code"
) -> dict[str, Any]:
    """LiteLLM's `kwargs` for one proxied call, as its logging callbacks receive them."""
    request = claude_code_request() if request is None else request
    return {
        "model": "claude-sonnet",
        "messages": request.get("messages"),
        "optional_params": {key: value for key, value in request.items() if key != "messages"},
        "litellm_params": {
            "metadata": {"user_api_key_metadata": {"run_id": RUN_ID, "backend": backend, "role": "implement"}}
        },
        "standard_logging_object": {
            "call_type": "anthropic_messages",
            "model_group": "claude-sonnet",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 40,
        },
    }


class FakeApi:
    """The API's internal routes as the callback speaks to them."""

    def __init__(self, registered: dict[str, Any] | None = None) -> None:
        self.registered = registered
        self.asked: list[str] = []
        self.posted: list[tuple[str, dict[str, Any]]] = []

    async def parts(self, run_id: str) -> Registry | None:
        self.asked.append(run_id)
        return None if self.registered is None else Registry.of(self.registered)

    async def post(self, run_id: str, breakdown: dict[str, Any]) -> bool:
        self.posted.append((run_id, breakdown))
        return True


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def measure(api: FakeApi, *, window_s: float = 5.0, clock: Clock | None = None) -> ContextMeasure:
    return ContextMeasure(api, window_s=window_s, clock=clock or Clock())


async def observe_all(context: ContextMeasure, *calls: dict[str, Any]) -> None:
    for kwargs in calls:
        context.observe(kwargs)
    await context.drain()


# ───────────────────────── the copy is the original ─────────────────────────


def test_the_callbacks_split_is_context_breakdowns_split() -> None:
    """The gateway's image has no Choregos package: the callback carries a copy of the split. On the
    Claude Code request, with and without parts, and on a chat-completions one, the two agree."""
    registered = registration()
    hints = core.registered_hints(registered["parts"], registered["mcp_servers"])
    copy = Registry.of(registered)
    chat = {
        "messages": [
            {"role": "system", "content": CLI_SYSTEM},
            {"role": "user", "content": f"{PLAYBOOK}\n\n{TASK}"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "crm__lookup", "arguments": "{}"}}]},
            {"role": "tool", "content": SKILL},
        ],
        "tools": [{"type": "function", "function": {"name": "crm__lookup", "description": "CRM"}}],
    }
    for body in (claude_code_request(), chat, {"input": TASK, "instructions": PLAYBOOK}):
        assert llm_spans.estimate_sections(body, copy.registered, copy.mcp_servers) == core.estimate_sections(
            body, hints
        )
        nothing = core.Hints(foreign=True)
        assert llm_spans.estimate_sections(body, {}, frozenset()) == core.estimate_sections(body, nothing)
    assert llm_spans.SECTIONS == core.SECTIONS
    assert (llm_spans.MIN_PARAGRAPH, llm_spans.PARAGRAPH.pattern) == (
        core.MIN_PARAGRAPH,
        core.PARAGRAPH.pattern,
    )
    assert llm_spans.WINDOW_S == core.WINDOW_S


def test_the_callbacks_scale_is_context_breakdowns_scale() -> None:
    draws = random.Random(64_08)
    for _ in range(500):
        estimates = {section: draws.choice([0, draws.randint(1, 40_000)]) for section in core.SECTIONS}
        total = draws.randint(0, 200_000)
        assert llm_spans.scale(estimates, total) == core.scale(estimates, total, fallback="backend")


# ───────────────────────── the split ─────────────────────────


def test_the_parts_the_runner_registered_are_recognised_and_the_rest_is_backend() -> None:
    registered = Registry.of(registration())
    estimates = llm_spans.estimate_sections(
        claude_code_request(), registered.registered, registered.mcp_servers
    )
    sections = llm_spans.scale(estimates, 30_000)

    assert sum(sections.values()) == 30_000
    assert sections["system"] > 0, "the playbook and CLAUDE.md"
    assert sections["messages"] > 0, "the task, and the CLI's own turns"
    assert sections["skills"] > 0, "the skill the agent read"
    assert sections["tools.mcp"] > 0, "mcp__crm__lookup: a server of the run"
    assert sections["backend"] > sections["system"], "Claude Code's own system prompt and tools"
    assert sections["tools.platform"] == sections["knowledge"] == sections["memory"] == 0
    # Without the registered paragraphs, the same request moves them to `backend`, nowhere else.
    bare = llm_spans.scale(llm_spans.estimate_sections(claude_code_request(), {}, frozenset({"crm"})), 30_000)
    assert bare["system"] == bare["skills"] == 0
    assert bare["backend"] > sections["backend"]


def test_a_request_with_nothing_recognised_is_all_backend_never_an_error() -> None:
    request = {
        "system": "A CLI of another vendor, in a shape of its own.",
        "tools": [{"name": "shell"}],
        "messages": [{"role": "user", "content": "Do the thing."}],
    }
    for body in (request, {}, {"messages": "a bare string"}, {"messages": [None, 3, {"role": "user"}]}):
        sections = llm_spans.scale(llm_spans.estimate_sections(body, {}, frozenset()), 1234)
        assert sum(sections.values()) == 1234
        assert sections["backend"] == 1234


# ───────────────────────── the callback ─────────────────────────


async def test_a_clis_call_is_posted_as_numbers_summing_to_the_gateways_total() -> None:
    api = FakeApi(registration())
    context = measure(api)

    await observe_all(context, call())

    ((run_id, posted),) = api.posted
    assert run_id == RUN_ID
    assert posted["source"] == "gateway" and posted["call"] == 1 and posted["model"] == "claude-sonnet"
    assert posted["prompt_tokens"] == 30_000 == sum(posted["sections"].values())
    assert posted["sections"]["system"] > 0 and posted["sections"]["backend"] > 0
    assert set(posted) == {"call", "at", "model", "prompt_tokens", "max_input_tokens", "source", "sections"}


async def test_no_text_and_no_digest_leaves_the_callback() -> None:
    api = FakeApi(registration())
    context = measure(api, window_s=0.0)

    await observe_all(context, call(), call(prompt_tokens=31_000))
    await context.close()

    sent = json.dumps(api.posted)
    assert CANARY in json.dumps(claude_code_request()), "the person's words reach the model"
    assert CANARY not in sent and "Claude Code" not in sent and "Pricing" not in sent
    assert not re.search(r"[0-9a-f]{64}", sent), "no digest of what was sent either"


async def test_the_parts_are_fetched_once_per_run() -> None:
    api = FakeApi(registration())
    context = measure(api, window_s=0.0)

    await observe_all(context, call(), call(), call())

    assert api.asked == [RUN_ID]


async def test_a_run_whose_parts_cannot_be_fetched_is_measured_all_in_backend() -> None:
    api = FakeApi(None)
    context = measure(api)

    await observe_all(context, call())

    ((_, posted),) = api.posted
    assert posted["sections"]["system"] == posted["sections"]["skills"] == 0
    assert posted["sections"]["tools.mcp"] == 0, "no server is known: the MCP tool too"
    assert sum(posted["sections"].values()) == 30_000


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(call(backend="prompt"), id="prompt-measured-by-the-loop"),
        pytest.param(call(backend="hosted"), id="hosted-measured-by-the-proxy"),
        pytest.param(call(backend="code"), id="code-not-measurable"),
        pytest.param({**call(), "litellm_params": {"metadata": {"user_api_key_metadata": {}}}}, id="no-run"),
        pytest.param(call(prompt_tokens=None), id="no-total"),
        pytest.param(call(prompt_tokens=0), id="zero-total"),
        pytest.param({**call(), "standard_logging_object": {"call_type": "aembedding"}}, id="embedding"),
        pytest.param(None, id="not-a-call"),
        pytest.param({"litellm_params": "garbage"}, id="garbage"),
    ],
)
async def test_what_is_not_a_clis_measured_call_is_left_alone(kwargs: Any) -> None:
    api = FakeApi(registration())
    context = measure(api)

    await observe_all(context, kwargs)

    assert api.posted == [] and api.asked == []


async def test_the_throttle_keeps_the_first_the_largest_of_each_window_and_the_last() -> None:
    clock = Clock()
    api = FakeApi(registration())
    context = measure(api, window_s=5.0, clock=clock)
    for at, tokens in [(0, 1000), (1, 9000), (2, 3000), (6, 2000), (7, 2500)]:
        clock.now = 100.0 + at
        await observe_all(context, call(prompt_tokens=tokens))

    assert [p["prompt_tokens"] for _, p in api.posted] == [1000, 9000], "the first, then the window's largest"
    await context.close()
    assert [(p["call"], p["prompt_tokens"]) for _, p in api.posted] == [
        (1, 1000),
        (2, 9000),
        (5, 2500),
    ], "the open window's largest is its last call"


async def test_the_last_call_is_posted_a_window_after_it_without_a_close() -> None:
    api = FakeApi(registration())
    context = ContextMeasure(api, window_s=0.05)

    await observe_all(context, call(prompt_tokens=1000), call(prompt_tokens=1200))
    assert [p["prompt_tokens"] for _, p in api.posted] == [1000]
    await asyncio.sleep(0.2)
    await context.drain()

    assert [p["prompt_tokens"] for _, p in api.posted] == [1000, 1200]


async def test_the_runs_kept_are_bounded() -> None:
    api = FakeApi(registration())
    context = ContextMeasure(api, max_runs=3)
    for number in range(10):
        kwargs = call()
        kwargs["litellm_params"]["metadata"]["user_api_key_metadata"]["run_id"] = f"run-{number}"
        await observe_all(context, kwargs)

    assert list(context.runs) == ["run-7", "run-8", "run-9"]


async def test_an_unreachable_api_never_raises_and_never_blocks_the_call() -> None:
    """Port 9 (discard) on the loopback: refused at once. The callback returns before the request is
    tried, and the attempt fails quietly."""
    handler = ChoregosLlmSpans(
        context=ContextMeasure(HttpContextApi("http://127.0.0.1:9/api/v1/internal", "k"))
    )
    assert handler.context is not None

    await handler.async_log_success_event(call(), None, None, None)
    assert handler.context.tasks, "the work is a task: the call did not wait for it"
    await handler.context.close()

    assert await HttpContextApi("http://127.0.0.1:9/x", "k", timeout_s=0.5).parts(RUN_ID) is None
    assert await HttpContextApi("http://127.0.0.1:9/x", "k", timeout_s=0.5).post(RUN_ID, {}) is False


async def test_a_failing_api_is_logged_never_raised() -> None:
    class Broken(FakeApi):
        async def post(self, run_id: str, breakdown: dict[str, Any]) -> bool:
            raise RuntimeError("boom")

    context = measure(Broken(registration()))
    await observe_all(context, call())
    await context.close()


def test_the_callback_is_off_unless_the_deployment_names_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(llm_spans.CONTEXT_URL_VARIABLE, raising=False)
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-master")
    assert llm_spans.context_from_environment() is None
    monkeypatch.setenv(llm_spans.CONTEXT_URL_VARIABLE, "file:///etc/passwd")
    assert llm_spans.context_from_environment() is None, "an http(s) URL only"
    monkeypatch.setenv(llm_spans.CONTEXT_URL_VARIABLE, "http://choregos-api:8000/api/v1/internal")
    assert isinstance(llm_spans.context_from_environment(), ContextMeasure)
    monkeypatch.delenv("LITELLM_MASTER_KEY")
    assert llm_spans.context_from_environment() is None


def test_the_signature_opens_one_run_only() -> None:
    signature = llm_spans.context_signature("sk-master", RUN_ID)
    assert signature != llm_spans.context_signature("sk-master", "another-run")
    assert signature != llm_spans.context_signature("sk-other", RUN_ID)
    assert "sk-master" not in signature


def test_registration_holds_no_text() -> None:
    registered = registration()
    sent = json.dumps(registered)
    assert CANARY not in sent and "discount" not in sent
    assert {part["section"] for part in registered["parts"]} == {"system", "messages", "skills"}
    assert all(set(part) == {"section", "sha256", "size"} for part in registered["parts"])
    assert core.register({"system": ["short"]}) == [], "a paragraph too short to tell apart is not registered"
    assert (
        len(
            core.register({"system": ["\n\n".join(f"paragraph number {n} " * 4 for n in range(50))]}, limit=7)
        )
        == 7
    )


def test_the_callback_imports_nothing_the_gateways_image_lacks() -> None:
    """LiteLLM's image has the standard library, the OpenTelemetry SDK and LiteLLM: a Choregos import
    (or any other) in the copy would stop the gateway from loading its callbacks."""
    tree = ast.parse(pathlib.Path(llm_spans.__file__).read_text(encoding="utf-8"))
    nodes = list(ast.walk(tree))
    imported = {
        alias.name.split(".")[0] for node in nodes if isinstance(node, ast.Import) for alias in node.names
    }
    imported |= {
        str(node.module).split(".")[0]
        for node in nodes
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
    }
    assert not [node for node in nodes if isinstance(node, ast.ImportFrom) and node.level > 0]
    assert imported - set(sys.stdlib_module_names) == {"opentelemetry"}
