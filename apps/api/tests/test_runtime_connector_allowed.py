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


@pytest.mark.parametrize(
    "config",
    [
        {"kubernetes": {"base_url": "https://k8s.elsewhere.example", "verify": False}},
        {"env_from_secrets": ["a-secret-of-the-namespace"]},
        {"image_pull_secrets": ["a-pull-secret"]},
        {"service_account": "another-account"},
    ],
    ids=lambda config: next(iter(config)),
)
async def test_a_runtime_never_sets_what_the_deployment_sets(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]
) -> None:
    """S65-03, ADR 0150: the cluster, and what a run's pod is given from it, are the deployment's."""
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    path = f"/api/v1/projects/{project['id']}/connectors/runtime"
    refused = await client.put(path, json={"type": "k8s_job", "config": config})
    assert refused.status_code == 422
    assert next(iter(config)) in refused.text and "deployment" in refused.text
    stored = (await client.get(f"/api/v1/projects/{project['id']}/connectors")).json()
    assert all(next(iter(config)) not in (row.get("config") or {}) for row in stored)


async def test_a_runtime_a_project_only_tunes_is_stored(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    path = f"/api/v1/projects/{project['id']}/connectors/runtime"
    assert (await client.put(path, json={"type": "k8s_job", "config": {"max_active": 2}})).status_code == 200
