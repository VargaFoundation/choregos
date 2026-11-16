"""What the runner registers for the gateway's context breakdown of an ACP CLI (ADR 0141 §4, S64-08b).

Before the agent starts, the runner journals `context.parts`: the digests and sizes of the paragraphs
it wrote — the playbook, `task.md`, `CLAUDE.md`, the skill's files — by section, and the run's MCP
servers; never the text. The gateway's callback, given that registration, recognises those paragraphs
in a request shaped as Claude Code sends it (built here from what the run really wrote and the prompt
the agent really received), puts the CLI's own system prompt and tools in `backend`, and sums to the
gateway's total.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import (
    AgentRef,
    LaunchSpec,
    McpServerRef,
    ModelRef,
    SkillRef,
    StageInput,
    ToolsRef,
    skill_digest,
)
from choregos_core.context_breakdown import PARTS_EVENT, paragraph_digest, paragraphs
from choregos_core.llm_spans import ContextMeasure, Registry
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, valid_result

pytestmark = pytest.mark.asyncio

#: Words of the ticket and of the playbook: they reach the agent, never the registration.
CANARY = "canary-6c55-in-the-ticket-only"
PLAYBOOK = (
    f"You implement the fix the ticket asks for, and nothing else. {CANARY}\n\n"
    "Run the tests before you write the result file, and say in its summary which tests you ran."
)
SKILL = {
    "SKILL.md": "---\nname: pricing\ndescription: The pricing rules.\n---\n\n# Pricing\n\n"
    "A credit note carries a negative amount; never round before the total, round the total once.\n",
}
DELIVERED = {"slug": "pricing", "version": 1, "digest": skill_digest(SKILL), "files": SKILL}


def _claude_code(stage_input: StageInput, prompts: Path) -> StageInput:
    stage = stage_input.model_copy(deep=True)
    stage.agent = AgentRef(
        backend="claude-code", launch=LaunchSpec(command=list(stage_input.agent.launch.command))
    )
    stage.model = ModelRef(
        litellm_model="anthropic/claude-sonnet", base_url="http://litellm:4000", api_format="anthropic"
    )
    stage.playbook = stage.playbook.model_copy(update={"prompt": PLAYBOOK})
    stage.work_item = stage.work_item.model_copy(update={"body": f"The total ignores credit notes. {CANARY}"})
    stage.tools = ToolsRef(mcp={"crm": McpServerRef(url="http://crm.internal/mcp")})
    stage.skills = [SkillRef(slug="pricing", version=1, digest=DELIVERED["digest"])]
    stage.agent.launch.env["FAKE_AGENT_PROMPTS"] = str(prompts)
    return stage


async def _run(stage_input: StageInput, runner_settings: Any, prompts: Path) -> FakeInternalClient:
    stage = _claude_code(stage_input, prompts)
    agent_script(
        {"turns": [{"messages": ["done"], "result": valid_result()}]}, stage, runner_settings.workspace
    )
    client = FakeInternalClient(stage)
    client.skills = [DELIVERED]
    outcome = await Runner(runner_settings, client).execute(stage, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    return client


async def test_the_runner_registers_what_it_wrote_before_the_agent_starts(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    client = await _run(stage_input, runner_settings, tmp_path / "prompts.json")

    types = [event["type"] for event in client.events]
    assert types.index(PARTS_EVENT) < types.index("session/new"), "registered before the first call"
    (registered,) = [event["payload"] for event in client.events if event["type"] == PARTS_EVENT]
    assert registered["mcp_servers"] == ["crm"]
    assert {part["section"] for part in registered["parts"]} == {"system", "messages", "skills"}
    workspace = Path(runner_settings.workspace)
    section_of = {part["sha256"]: part["section"] for part in registered["parts"]}
    for text, section in [
        (PLAYBOOK, "system"),
        ((workspace / "CLAUDE.md").read_text(encoding="utf-8"), "system"),
        ((workspace / ".choregos" / "task.md").read_text(encoding="utf-8"), "messages"),
        (SKILL["SKILL.md"], "skills"),
    ]:
        assert paragraphs(text), section
        assert {section_of.get(paragraph_digest(paragraph)) for paragraph in paragraphs(text)} == {section}
    sent = json.dumps(registered)
    assert CANARY not in sent and "credit note" not in sent and "Instructions (Choregos)" not in sent


async def test_the_gateway_recognises_what_the_runner_registered_in_a_claude_code_request(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    client = await _run(stage_input, runner_settings, tmp_path / "prompts.json")
    (registered,) = [event["payload"] for event in client.events if event["type"] == PARTS_EVENT]
    workspace = Path(runner_settings.workspace)
    (first_prompt, *_) = json.loads((tmp_path / "prompts.json").read_text(encoding="utf-8"))
    assert CANARY in first_prompt, "the ticket and the playbook reach the agent"
    claude_md = (workspace / "CLAUDE.md").read_text(encoding="utf-8")
    skill = (workspace / ".claude" / "skills" / "pricing" / "SKILL.md").read_text(encoding="utf-8")
    request = {
        "system": [
            {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude. " * 30}
        ],
        "tools": [
            {"name": "Bash", "description": "Executes a bash command. " * 30, "input_schema": {}},
            {"name": "mcp__crm__lookup", "description": "Look up a customer. " * 10, "input_schema": {}},
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"<system-reminder>\nContents of {workspace}/CLAUDE.md:\n\n{claude_md}\n\n"
                        "</system-reminder>",
                    },
                    {"type": "text", "text": first_prompt},
                ],
            },
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": skill}]},
        ],
    }

    class Api:
        def __init__(self) -> None:
            self.posted: list[dict[str, Any]] = []

        async def parts(self, run_id: str) -> Registry | None:
            return Registry.of(registered)

        async def post(self, run_id: str, breakdown: dict[str, Any]) -> bool:
            self.posted.append(breakdown)
            return True

    api = Api()
    measure = ContextMeasure(api)
    measure.observe(
        {
            "messages": request["messages"],
            "optional_params": {"system": request["system"], "tools": request["tools"]},
            "litellm_params": {
                "metadata": {"user_api_key_metadata": {"run_id": "run-test-1", "backend": "claude-code"}}
            },
            "standard_logging_object": {"call_type": "anthropic_messages", "prompt_tokens": 24_680},
        }
    )
    await measure.close()

    (posted,) = api.posted
    sections = posted["sections"]
    assert sum(sections.values()) == 24_680
    for section in ("system", "messages", "skills", "tools.mcp", "backend"):
        assert sections[section] > 0, section
    assert sections["tools.platform"] == sections["knowledge"] == sections["memory"] == 0
    assert CANARY not in json.dumps(api.posted)
