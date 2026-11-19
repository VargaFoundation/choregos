"""S4-08: each direct gateway call of the API marks its stable prefix (ADR 0189)."""

from __future__ import annotations

import json
from typing import Any

import httpx
from tranibor_api.knowledge.distill import GatewayDistiller
from tranibor_api.services.llm_routing import Candidate, GatewayWorkflowRouter, Item

CLAUDE = "anthropic/claude-sonnet-5"
COMPLETION = {"choices": [{"message": {"content": '{"workflow": "a", "reason": "r"}'}}], "usage": {}}


def _recorder(bodies: list[dict[str, Any]]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=COMPLETION)

    return httpx.MockTransport(handle)


async def test_the_workflow_router_marks_its_instructions_and_keeps_the_item_after_them() -> None:
    bodies: list[dict[str, Any]] = []
    router = GatewayWorkflowRouter("http://g", "k", CLAUDE, transport=_recorder(bodies))
    candidates = [Candidate("a", "first", None)]
    await router.choose(candidates, Item("Leaver 1", "at 10:00", (), None))
    await router.choose(candidates, Item("Leaver 2", "at 11:00", (), None))
    first, second = bodies
    assert first["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["messages"][0] == second["messages"][0], "the prefix does not move between two items"
    assert first["messages"][1] != second["messages"][1]
    assert isinstance(first["messages"][1]["content"], str)


async def test_the_workflow_router_sends_no_cache_field_to_another_provider() -> None:
    bodies: list[dict[str, Any]] = []
    router = GatewayWorkflowRouter("http://g", "k", "openai/gpt-5", transport=_recorder(bodies))
    await router.choose([Candidate("a", None, None)], Item("t", None, (), None))
    assert "cache_control" not in json.dumps(bodies[0])


async def test_the_distiller_marks_its_system_prompt() -> None:
    bodies: list[dict[str, Any]] = []
    distiller = GatewayDistiller("http://g", "k", CLAUDE, transport=_recorder(bodies))
    await distiller._ask("SYSTEM " * 50, "volatile")
    assert bodies[0]["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert bodies[0]["messages"][1]["content"] == "volatile"
