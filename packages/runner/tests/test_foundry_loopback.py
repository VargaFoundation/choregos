"""An agent written for Foundry runs unchanged against the runner's loopback model (S41-19).

`sidecar_env` gives the container Foundry's variables: a project endpoint and a managed identity, both
on the model listener. The model proxy serves the project's OpenAI root, its conversations and App
Service's identity protocol; the token it hands out is the loopback key, which the gateway never sees.
The real SDKs (`azure-identity`, `azure-ai-projects`, Agent Framework's Foundry client and hosting
adapter) are run by the conformance suite on `examples/hosted/agent-framework-foundry`.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
import pytest
from choregos_contracts import AgentRef, HostedCall, ModelRef, StageInput
from choregos_core.hosted import IDENTITY_HEADER, LOOPBACK_KEY, sidecar_env
from choregos_runner.loopback.model import ModelProxy

pytestmark = pytest.mark.asyncio

#: Built at run time: a literal that looks like a key would trip the secrets scan of the CI.
KEY = "-".join(["sk", "run", "4b8d" * 4])
IMAGE = "registry.example/acme/triage@sha256:" + "a" * 64
HOSTED = HostedCall(image=IMAGE, agent_slug="triage", agent_version=3)
LOOPBACK = {"authorization": f"Bearer {LOOPBACK_KEY}"}


class Gateway:
    """What reaches the gateway, answered with an empty completion."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"id": "x", "object": "ok"})


def _stage(stage_input: StageInput, max_turns: int = 2) -> StageInput:
    return stage_input.model_copy(
        update={
            "agent": AgentRef(backend="hosted"),
            "model": ModelRef(litellm_model="platform/standard", base_url="http://gateway:4000/v1"),
            "gateway_key": KEY,
            "hosted": HOSTED,
            "budget": stage_input.budget.model_copy(update={"max_turns": max_turns}),
        }
    )


async def _proxy(
    stage_input: StageInput, max_turns: int = 2
) -> tuple[ModelProxy, Gateway, httpx.AsyncClient, list[tuple[str, dict[str, Any]]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []

    async def record(type_: str, payload: dict[str, Any]) -> None:
        recorded.append((type_, payload))

    gateway = Gateway()
    proxy = ModelProxy(_stage(stage_input, max_turns), record, transport=httpx.MockTransport(gateway))
    container = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy.app()), base_url="http://127.0.0.1:4001"
    )
    return proxy, gateway, container, recorded


async def test_the_container_gets_foundrys_variables_on_the_loopback() -> None:
    env = sidecar_env(HOSTED, run_id="run-1", model="platform/standard")

    assert env["FOUNDRY_PROJECT_ENDPOINT"] == "http://127.0.0.1:4001/api/projects/choregos"
    assert env["FOUNDRY_MODEL"] == "platform/standard"
    assert (env["FOUNDRY_AGENT_NAME"], env["FOUNDRY_AGENT_VERSION"]) == ("triage", "3")
    assert env["FOUNDRY_AGENT_SESSION_ID"] == "run-1", "a step's container lives for its run"
    assert env["IDENTITY_ENDPOINT"] == "http://127.0.0.1:4001/msi/token"
    assert env["IDENTITY_HEADER"] == IDENTITY_HEADER
    assert "FOUNDRY_HOSTING_ENVIRONMENT" not in env, "the project's storage APIs are not served"
    # A GUID, as Foundry's, and the same for every version of the agent.
    agent_id = env["FOUNDRY_AGENT_ID"]
    assert str(uuid.UUID(agent_id)) == agent_id
    later = HOSTED.model_copy(update={"agent_version": 4})
    assert sidecar_env(later, run_id="run-2", model="m")["FOUNDRY_AGENT_ID"] == agent_id
    other = HOSTED.model_copy(update={"agent_slug": "billing"})
    assert sidecar_env(other, run_id="run-2", model="m")["FOUNDRY_AGENT_ID"] != agent_id


async def test_a_session_pods_container_gets_the_sessions_id() -> None:
    env = sidecar_env(HOSTED, run_id="session-0b1c", model="platform/standard")
    assert env["FOUNDRY_AGENT_SESSION_ID"] == "0b1c"


async def test_the_version_cannot_override_foundrys_variables() -> None:
    hosted = HOSTED.model_copy(
        update={"env": {"FOUNDRY_PROJECT_ENDPOINT": "https://x.example", "IDENTITY_HEADER": "h"}}
    )
    env = sidecar_env(hosted, run_id="run-1", model="m")
    assert env["FOUNDRY_PROJECT_ENDPOINT"].startswith("http://127.0.0.1:4001/")
    assert env["IDENTITY_HEADER"] == IDENTITY_HEADER


async def test_the_managed_identity_hands_out_the_loopback_key(stage_input: StageInput) -> None:
    _, gateway, container, recorded = await _proxy(stage_input)
    async with container:
        before = int(time.time())
        answer = await container.get(
            "/msi/token",
            params={"api-version": "2019-08-01", "resource": "https://ai.azure.com"},
            headers={"x-identity-header": IDENTITY_HEADER},
        )
    assert answer.status_code == 200
    token = answer.json()
    assert (token["access_token"], token["token_type"]) == (LOOPBACK_KEY, "Bearer")
    assert token["resource"] == "https://ai.azure.com"
    assert int(token["expires_on"]) > before + 600, "the SDKs would ask again at every call"
    assert recorded == [("hosted.identity_token", {"resource": "https://ai.azure.com"})]
    assert not gateway.requests


async def test_the_identity_wants_its_header_and_a_get(stage_input: StageInput) -> None:
    _, gateway, container, recorded = await _proxy(stage_input)
    async with container:
        bare = await container.get("/msi/token", params={"resource": "https://ai.azure.com"})
        wrong = await container.get("/msi/token", headers={"x-identity-header": "guess"})
        posted = await container.post("/msi/token", headers={"x-identity-header": IDENTITY_HEADER})
    assert (bare.status_code, wrong.status_code, posted.status_code) == (401, 401, 405)
    assert LOOPBACK_KEY not in bare.text + wrong.text + posted.text
    assert not recorded and not gateway.requests


async def test_a_foundry_project_call_is_a_model_call_with_the_runs_key(stage_input: StageInput) -> None:
    proxy, gateway, container, _ = await _proxy(stage_input)
    async with container:
        answer = await container.post(
            "/api/projects/choregos/openai/v1/responses",
            headers=LOOPBACK,
            json={"model": "gpt-4.1", "input": "ping"},
        )
    assert answer.status_code == 200
    (sent,) = gateway.requests
    assert str(sent.url) == "http://gateway:4000/v1/responses"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert LOOPBACK_KEY not in json.dumps(dict(sent.headers)), "the identity's token stays in the pod"
    assert json.loads(sent.content)["model"] == "platform/standard"
    assert proxy.turns == 1


async def test_another_path_of_the_project_is_not_served(stage_input: StageInput) -> None:
    _, gateway, container, _ = await _proxy(stage_input)
    async with container:
        files = await container.post("/api/projects/choregos/openai/v1/files", headers=LOOPBACK, json={})
        agents = await container.get("/api/projects/choregos/agents", headers=LOOPBACK)
    assert (files.status_code, agents.status_code) == (404, 404)
    assert not gateway.requests


async def test_conversations_are_forwarded_and_are_not_turns(stage_input: StageInput) -> None:
    proxy, gateway, container, _ = await _proxy(stage_input, max_turns=1)
    root = "/api/projects/choregos/openai/v1"
    async with container:
        assert (await container.post(f"{root}/responses", headers=LOOPBACK, json={})).status_code == 200
        # The step has used its only turn; its stored conversations still answer.
        created = await container.post(f"{root}/conversations", headers=LOOPBACK, json={"metadata": {}})
        items = await container.get(
            f"{root}/conversations/conv_1/items", headers=LOOPBACK, params={"limit": 2}
        )
        deleted = await container.delete(f"{root}/conversations/conv_1", headers=LOOPBACK)
    assert [r.status_code for r in (created, items, deleted)] == [200, 200, 200]
    sent = gateway.requests[1:]
    assert [(r.method, r.url.path, r.url.query) for r in sent] == [
        ("POST", "/v1/conversations", b""),
        ("GET", "/v1/conversations/conv_1/items", b"limit=2"),
        ("DELETE", "/v1/conversations/conv_1", b""),
    ]
    assert json.loads(sent[0].content) == {"metadata": {}}, "no model is put into a conversation"
    assert all(r.headers["authorization"] == f"Bearer {KEY}" for r in sent)
    assert proxy.turns == 1


async def test_conversations_take_the_loopback_key_only(stage_input: StageInput) -> None:
    _, gateway, container, _ = await _proxy(stage_input)
    async with container:
        answer = await container.post("/v1/conversations", headers={"authorization": "Bearer x"}, json={})
    assert answer.status_code == 401
    assert not gateway.requests


async def test_delete_is_served_for_conversations_only(stage_input: StageInput) -> None:
    proxy, gateway, container, _ = await _proxy(stage_input)
    async with container:
        answer = await container.delete("/v1/responses", headers=LOOPBACK)
    assert answer.status_code == 405
    assert not gateway.requests and proxy.turns == 0
