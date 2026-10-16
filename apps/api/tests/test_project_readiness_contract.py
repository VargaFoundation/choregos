"""S40-06a: the project readiness contract is declared and served."""

from __future__ import annotations

from typing import Any

import choregos_contracts as contracts


def test_the_contract_declares_the_readiness_operation() -> None:
    operation = contracts.load_openapi()["paths"]["/projects/{id}/readiness"]["get"]
    assert operation["operationId"] == "getProjectReadiness"
    assert "200" in operation["responses"] and "404" in operation["responses"]


def test_a_readiness_item_says_who_can_fix_it() -> None:
    schemas = contracts.load_openapi()["components"]["schemas"]
    item = schemas["ReadinessItem"]
    assert set(item["required"]) == {"id", "section", "title", "state", "detail", "fix"}
    assert set(item["properties"]["state"]["enum"]) == {"done", "check", "todo", "not_needed"}
    assert set(schemas["ReadinessFix"]["properties"]["by"]["enum"]) == {
        "project_owner",
        "org_admin",
        "platform_operator",
        "external",
    }
    assert set(item["properties"]["section"]["enum"]) == {
        "general",
        "connections",
        "agents",
        "guardrails",
        "people",
        "notifications",
        "tools",
    }


async def test_the_route_needs_a_signed_in_person(client: Any, project: dict[str, Any]) -> None:
    """The stub of S40-06a became a real route in S40-06b: it answers, and it needs a session."""
    client.cookies.clear()
    response = await client.get(f"/api/v1/projects/{project['id']}/readiness")
    assert response.status_code == 401
