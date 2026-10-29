"""What a workflow actor that names no registry agent runs (the map's step card, the agents page).

The console used to say "built-in playbook, not a registry agent" and nothing else: no prompt, no
model, no tools. The endpoint answers with the runtime's own pieces — the role's playbook, the
model resolver, the policy's limits, the run's MCP servers — so the card says what a run does.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def _describe(client: AsyncClient, project_id: str, **params: Any) -> Any:
    answer = await client.get(f"/api/v1/projects/{project_id}/builtin-agent", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_the_role_s_prompt_is_what_a_run_receives(client: AsyncClient, project: dict[str, Any]) -> None:
    agent = await _describe(client, project["id"], role="implement")
    assert (agent["playbook"], agent["playbook_origin"]) == ("implement", "package")
    assert "You implement the specification" in agent["instructions"]
    assert "<work item title>" in agent["instructions"], "what only a run knows is named, not invented"
    assert "## Invariants" in agent["instructions"]
    template = agent["instructions_template"]
    assert "You implement the specification" in template and "{{ ticket.get('title', '') }}" in template
    assert "{{ output_contract }}" not in template, "the frame adds it: it would reach the agent twice"


async def test_by_size_names_the_model_of_every_size(client: AsyncClient, project: dict[str, Any]) -> None:
    configured = await client.put(
        f"/api/v1/projects/{project['id']}/models",
        json={
            "profiles": {"strong": {"litellm_model": "anthropic/claude-opus-test"}},
            "allow_unvalidated": True,
        },
    )
    assert configured.status_code == 200, configured.text
    agent = await _describe(client, project["id"], role="implement")
    by_size = {choice["size"]: choice for choice in agent["models"]}
    assert list(by_size) == ["S", "M", "L", "XL"]
    assert (by_size["S"]["profile"], by_size["S"]["litellm_model"]) == ("standard", "platform/standard")
    assert (by_size["L"]["profile"], by_size["L"]["litellm_model"]) == (
        "strong",
        "anthropic/claude-opus-test",
    )
    assert by_size["XL"]["turns_factor"] == 1.5


async def test_a_fixed_model_is_one_answer_and_the_actor_s_fields_win(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    agent = await _describe(
        client, project["id"], role="review", model="profile:cheap", backend="codex", max_turns=12
    )
    assert [(c["size"], c["profile"]) for c in agent["models"]] == [(None, "cheap")]
    assert (agent["backend"], agent["backend_source"]) == ("codex", "actor")
    assert (agent["max_turns"], agent["limits_source"]) == (12, "actor")


async def test_the_defaults_come_from_the_project_and_the_policy(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    agent = await _describe(client, project["id"], role="verify")
    assert (agent["backend"], agent["backend_source"]) == ("claude-code", "project")
    assert agent["limits_source"] == "policy" and agent["max_turns"] >= 1 and agent["max_minutes"] >= 1
    servers = {server["name"]: server for server in agent["mcp_servers"]}
    assert {"report_finding", "ask_human", "request_scope_change"} <= set(servers["choregos"]["tools"])
    assert agent["memory"] == ["project:shared (write)"]


async def test_an_unknown_playbook_is_said_not_raised(client: AsyncClient, project: dict[str, Any]) -> None:
    agent = await _describe(client, project["id"], role="implement", playbook="unicorn")
    assert agent["instructions"] is None
    assert "unknown playbook" in agent["instructions_error"]


async def test_a_name_cannot_leave_the_playbook_directories(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    answer = await client.get(
        f"/api/v1/projects/{project['id']}/builtin-agent",
        params={"role": "implement", "playbook": "../_base"},
    )
    assert answer.status_code == 422


async def test_the_workflow_agents_list_named_and_built_in_ones(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    agents = (await client.get(f"/api/v1/projects/{project['id']}/workflow-agents")).json()
    implicit = (await client.get(f"/api/v1/projects/{project['id']}/implicit-agents")).json()
    assert agents, "the project's default workflow runs agents"
    built_in = [(a["workflow"], a["actor"]) for a in agents if not a["registered"]]
    assert built_in == [(a["workflow"], a["actor"]) for a in implicit], (
        "the implicit list is the unregistered part"
    )
