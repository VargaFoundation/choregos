"""Train status answers from the database when its workflow is absent or silent (S26-01).

A train workflow `train-<slug>-<env>` is started lazily, on the first merge. Before S26-01, asking a
project with no merge for its train status asked Temporal for a workflow that does not exist, the
client raised `RPCError: workflow not found`, and the API answered 500. A train whose history is
long to replay did the same after tens of seconds ("query task not found, or already expired").
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from choregos_api import temporal as module
from choregos_api.temporal import FakeTemporal, RealTemporal, get_temporal, train_id
from httpx import AsyncClient
from temporalio.client import WorkflowQueryFailedError
from temporalio.service import RPCError, RPCStatusCode


async def test_a_project_with_no_merge_has_a_collecting_train(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    with pytest.raises(RPCError) as raised:  # the fake behaves like the real client
        await fake._query(train_id("billing-api", "prod"), "status_query")
    assert raised.value.status == RPCStatusCode.NOT_FOUND

    response = await client.get(f"/api/v1/projects/{project['id']}/trains/prod")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "collecting" and body["batch_size"] == 0 and body["pending_items"] == []


async def test_the_fake_answers_a_started_workflow_without_a_scripted_answer() -> None:
    fake = FakeTemporal()
    await fake.start_train("train-billing-prod", {})
    assert await fake.query("train-billing-prod", "status_query") is None


class _Handle:
    def __init__(self, behaviour: Any) -> None:
        self.behaviour = behaviour

    async def query(self, name: str) -> Any:
        return await self.behaviour()


class _Client:
    def __init__(self, handle: _Handle) -> None:
        self.handle = handle

    def get_workflow_handle(self, workflow_id: str) -> _Handle:
        return self.handle


def _real(behaviour: Any) -> RealTemporal:
    temporal = RealTemporal("temporal:7233", "default", "orchestrator")
    temporal._client = _Client(_Handle(behaviour))
    return temporal


def _raise(exc: BaseException) -> Any:
    async def behaviour() -> Any:
        raise exc

    return behaviour


@pytest.mark.parametrize(
    "exc",
    [
        RPCError("workflow not found for ID: train-billing-prod", RPCStatusCode.NOT_FOUND, b""),
        RPCError("query task not found, or already expired", RPCStatusCode.NOT_FOUND, b""),
        RPCError("context deadline exceeded", RPCStatusCode.DEADLINE_EXCEEDED, b""),
        WorkflowQueryFailedError("unknown query type"),
    ],
)
async def test_the_real_client_answers_none_when_the_workflow_cannot_answer(exc: BaseException) -> None:
    assert await _real(_raise(exc)).query("train-billing-prod", "status_query") is None


async def test_the_real_client_does_not_wait_for_a_silent_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    async def silent() -> Any:
        await asyncio.sleep(10)

    monkeypatch.setattr(module, "QUERY_TIMEOUT_S", 0.01)
    assert await asyncio.wait_for(_real(silent).query("train-billing-prod", "status_query"), 1.0) is None


async def test_the_real_client_still_raises_what_is_not_an_absence() -> None:
    refused = RPCError("permission denied", RPCStatusCode.PERMISSION_DENIED, b"")
    with pytest.raises(RPCError):
        await _real(_raise(refused)).query("train-billing-prod", "status_query")


async def test_the_real_client_returns_the_answer() -> None:
    async def answer() -> Any:
        return {"status": "collecting"}

    assert await _real(answer).query("train-billing-prod", "status_query") == {"status": "collecting"}
