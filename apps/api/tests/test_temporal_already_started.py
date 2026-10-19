"""Starting a workflow that already runs is not an error (S39-29).

On the dev tenant (0.18.4) a "sync now" on a knowledge source whose sync loop already ran answered
500: the SDK raises `WorkflowAlreadyStartedError`, which is not an `RPCError`, and `_start` only
caught the latter. `FakeTemporal` never raises, so no API test saw it.
"""

from __future__ import annotations

from typing import Any

import pytest


class _Client:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[str] = []

    async def start_workflow(self, workflow: str, payload: Any, **kwargs: Any) -> Any:
        self.calls.append(str(kwargs["id"]))
        raise self.error


@pytest.mark.parametrize(
    "start",
    ["start_knowledge_sync", "start_interpreter", "start_train", "start_action", "start_provisioning"],
)
async def test_an_already_started_workflow_returns_its_id(start: str) -> None:
    from choregos_api.temporal import RealTemporal
    from temporalio.exceptions import WorkflowAlreadyStartedError

    temporal = RealTemporal("temporal:7233", "default", "orchestrator")
    client = _Client(WorkflowAlreadyStartedError("ks-1", "KnowledgeSync"))

    async def connected() -> Any:
        return client

    temporal.client = connected  # type: ignore[method-assign]
    assert await getattr(temporal, start)("ks-1", {}) == "ks-1"
    assert client.calls == ["ks-1"]


async def test_another_start_failure_still_raises() -> None:
    from choregos_api.temporal import RealTemporal

    temporal = RealTemporal("temporal:7233", "default", "orchestrator")
    client = _Client(RuntimeError("the namespace does not exist"))

    async def connected() -> Any:
        return client

    temporal.client = connected  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="namespace"):
        await temporal.start_knowledge_sync("ks-1", {})
