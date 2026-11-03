"""A policy with `memory.enabled: false` wins over an agent that asks for memory (S51-01).

The run binds nothing (the orchestrator's test); here, the platform says it ahead of time without
refusing anything (ADR 0007: memory never blocks): the project validation warns on the actor, the
publication and the policy that turns memory off log `policy.memory_disabled`.
"""

from __future__ import annotations

from typing import Any

import structlog
from httpx import AsyncClient

from .agent_scopes_kit import ORG

POLICY_WITHOUT_MEMORY = """
apiVersion: choregos/v1
kind: Policy
metadata: { name: no-memory, version: 2 }
memory: { enabled: false }
"""


def _workflow(agent: str) -> str:
    from choregos_core.dsl import template_yaml

    yaml = template_yaml("default-simple").replace(
        'dev: { type: agent, role: implement, model: "profile:by_size" }',
        f'dev: {{ type: agent, role: implement, model: "profile:by_size", agent: {agent} }}',
    )
    assert f"agent: {agent}" in yaml
    return yaml


async def _agent(client: AsyncClient, slug: str, memory: str) -> None:
    spec = {"instructions": "Review the access rights.", "memory": memory}
    response = await client.post(f"{ORG}/agents", json={"slug": slug, "display_name": slug, "spec": spec})
    assert response.status_code == 201, response.text


def _logged(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("event") == "policy.memory_disabled"]


async def test_a_workflow_naming_an_agent_with_memory_under_a_policy_without_is_warned_not_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _agent(client, "fon-access-analyst", "project")
    await _agent(client, "forgetful", "none")
    validate = f"/api/v1/projects/{pid}/workflows/validate"

    enabled = (await client.post(validate, json={"yaml": _workflow("fon-access-analyst")})).json()
    assert [w for w in enabled["warnings"] if w["code"] == "agent.memory_disabled_by_policy"] == []

    with structlog.testing.capture_logs() as events:
        put = await client.put(f"/api/v1/projects/{pid}/policy", json={"yaml": POLICY_WITHOUT_MEMORY})
    assert put.status_code == 200, put.text
    assert _logged(events) == [], "no active workflow names the agent yet"

    validation = (await client.post(validate, json={"yaml": _workflow("fon-access-analyst")})).json()
    assert validation["valid"] is True
    (warning,) = [w for w in validation["warnings"] if w["code"] == "agent.memory_disabled_by_policy"]
    assert warning["path"] == "/actors/dev/agent"
    assert "fon-access-analyst" in warning["message"] and "it will get none" in warning["message"]

    with structlog.testing.capture_logs() as events:
        published = await client.put(
            f"/api/v1/projects/{pid}/workflows/default-simple", json={"yaml": _workflow("fon-access-analyst")}
        )
    assert published.status_code == 200, published.text
    (logged,) = _logged(events)
    assert logged["log_level"] == "warning" and logged["agent"] == "fon-access-analyst"
    assert logged["project"] == project["slug"]

    none = (await client.post(validate, json={"yaml": _workflow("forgetful")})).json()
    assert [w for w in none["warnings"] if w["code"] == "agent.memory_disabled_by_policy"] == [], (
        "an agent that asks for no memory loses nothing"
    )


async def test_a_policy_that_turns_memory_off_under_a_named_agent_is_logged(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _agent(client, "fon-access-analyst", "project")
    published = await client.put(
        f"/api/v1/projects/{pid}/workflows/default-simple", json={"yaml": _workflow("fon-access-analyst")}
    )
    assert published.status_code == 200, published.text
    with structlog.testing.capture_logs() as events:
        put = await client.put(f"/api/v1/projects/{pid}/policy", json={"yaml": POLICY_WITHOUT_MEMORY})
    assert put.status_code == 200, put.text
    (logged,) = _logged(events)
    assert logged["agent"] == "fon-access-analyst" and logged["workflow"] == "default-simple"
    assert "memory.enabled: false" in logged["message"]
