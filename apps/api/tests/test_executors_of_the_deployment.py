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


async def test_elsewhere_the_docker_executor_stays_off(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHOREGOS_EXECUTOR_KIND", raising=False)
    enabled = await _enabled(client)
    assert enabled["local_docker"] is False
    assert enabled["tekton"] is True and enabled["k8s_job"] is True
