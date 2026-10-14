"""The demo organisation of the local install (`python -m choregos_api.local_demo`, S28-01)."""

from __future__ import annotations

from typing import Any

from choregos_api.config import Settings
from choregos_api.local_demo import DEMO_WORKFLOW, demo_connectors, seed_local_demo
from choregos_core.dsl.parser import parse_workflow
from httpx import AsyncClient

from .conftest import login


def _settings() -> Settings:
    return Settings(bootstrap_org="demo", bootstrap_org_name="Demo")


def test_the_demo_workflow_is_valid() -> None:
    workflow, report = parse_workflow(DEMO_WORKFLOW, strict=True)
    assert report.valid, report.errors
    assert workflow.metadata.name == "local-demo"


def test_fakes_mode_simulates_the_agent_and_keeps_the_tracker_inside_choregos() -> None:
    connectors = demo_connectors()
    assert connectors["runtime"] == ("fake", {"simulate": True})
    assert connectors["tracker"] == ("internal", {})


async def test_the_seed_creates_the_project_once(app: Any) -> None:
    first = await seed_local_demo(_settings())
    assert first["organisations"] == ["demo"]
    assert first["projects"] == ["demo"]
    assert sorted(first["connectors"]) == sorted(demo_connectors())

    again = await seed_local_demo(_settings())
    assert again == {"organisations": [], "projects": [], "connectors": []}


async def test_a_request_created_in_the_demo_project_starts_its_workflow(
    app: Any, client: AsyncClient
) -> None:
    from choregos_api.temporal import get_temporal

    await seed_local_demo(_settings())
    await login(client, "admin@varga.dev")  # a dev admin of every organisation of the bench
    created = await client.post(
        "/api/v1/projects/demo:demo/work-items", json={"title": "Plan the team offsite", "body": "Lyon, May."}
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert (item["workflow_name"], item["state"]) == ("local-demo", "inbox")
    assert item["temporal_wf_id"] in get_temporal().started  # type: ignore[attr-defined]
