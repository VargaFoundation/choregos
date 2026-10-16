"""ADR 0072: a project cannot pick a runtime its deployment does not run.

A project could write `tekton` as its `runtime` on a cluster without Tekton, and its first run
died. The deployment declares what it runs (`CHOREGOS_EXECUTOR_KIND`,
`CHOREGOS_EXECUTORS_ALLOWED`); the API refuses the rest, and names what is allowed.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


async def _put(client: AsyncClient, project: dict[str, Any], type_name: str) -> Any:
    return await client.put(
        f"/api/v1/projects/{project['id']}/connectors/runtime", json={"type": type_name, "config": {}}
    )


async def test_a_runtime_the_deployment_does_not_run_is_refused(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "agent_sandbox")
    monkeypatch.setenv("CHOREGOS_EXECUTORS_ALLOWED", "agent_sandbox,k8s_job")
    refused = await _put(client, project, "tekton")
    assert refused.status_code == 422
    assert "agent_sandbox, k8s_job" in refused.text and "allowedExecutors" in refused.text


async def test_an_allowed_runtime_is_stored(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "agent_sandbox")
    monkeypatch.setenv("CHOREGOS_EXECUTORS_ALLOWED", "agent_sandbox,k8s_job")
    assert (await _put(client, project, "k8s_job")).status_code == 200


async def test_a_server_that_declares_nothing_refuses_nothing(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHOREGOS_EXECUTOR_KIND", raising=False)
    monkeypatch.delenv("CHOREGOS_EXECUTORS_ALLOWED", raising=False)
    assert (await _put(client, project, "tekton")).status_code == 200


async def test_the_requirements_name_the_deployment_s_runtime(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    items = (await client.get(f"/api/v1/projects/{project['id']}/requirements")).json()
    runtime = next(item for item in items if item["capability"] == "runtime")
    assert runtime["default_type"] == "k8s_job"
