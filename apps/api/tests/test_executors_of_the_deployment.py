"""`GET /platform/executors` says what the deployment runs (S28-02 finding).

With no executor row in the database, the list was written by hand: `local_docker` was always
`enabled: false` — also on the local install (`compose.yaml`), whose workers run every agent
through it (`CHOREGOS_EXECUTOR_KIND=local_docker`).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _enabled(client: AsyncClient) -> dict[str, bool]:
    return {e["kind"]: e["enabled"] for e in (await client.get("/api/v1/platform/executors")).json()}


async def test_the_local_install_reports_its_docker_executor_as_enabled(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "local_docker")
    assert (await _enabled(client))["local_docker"] is True


async def test_only_the_deployment_s_executors_are_enabled(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0072: with no row, the list is the deployment's, not a hand-written one (it said
    Tekton was on while the workers ran Jobs)."""
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "agent_sandbox")
    monkeypatch.setenv("CHOREGOS_EXECUTORS_ALLOWED", "agent_sandbox,k8s_job")
    enabled = await _enabled(client)
    assert enabled == {
        "agent_sandbox": True,
        "k8s_job": True,
        "tekton": False,
        "aca": False,
        "local_docker": False,
    }


async def test_a_bare_server_runs_agent_sandbox(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHOREGOS_EXECUTOR_KIND", raising=False)
    monkeypatch.delenv("CHOREGOS_EXECUTORS_ALLOWED", raising=False)
    enabled = await _enabled(client)
    assert enabled["agent_sandbox"] is True
    assert enabled["tekton"] is False and enabled["local_docker"] is False
