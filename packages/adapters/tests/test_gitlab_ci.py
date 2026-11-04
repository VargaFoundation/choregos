"""The GitLab CI adapter against its documented API, request by request (respx mocks)."""

from __future__ import annotations

import httpx
import respx
from choregos_adapters import connector_types
from choregos_adapters.ci.gitlab import GitLabCi
from choregos_adapters.http import RestClient

BASE = "https://gitlab.com/api/v4/projects/varga%2Fbilling-api"


def adapter() -> GitLabCi:
    client = RestClient("https://gitlab.com", headers={"PRIVATE-TOKEN": "tok"}, service="gitlab")
    return GitLabCi(client, "varga/billing-api")


@respx.mock
async def test_status_maps_the_latest_pipeline() -> None:
    pipelines = respx.get(f"{BASE}/pipelines").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 12, "status": "failed", "web_url": "https://gitlab.com/p/12"},
                {"id": 9, "status": "success"},
            ],
        )
    )
    respx.get(f"{BASE}/pipelines/12/jobs").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "lint", "status": "success", "web_url": "https://gitlab.com/j/1"},
                {"name": "test", "status": "failed"},
                {"name": "deploy", "status": "pending"},
            ],
        )
    )
    status = await adapter().status_for("varga/billing-api", "abc123")
    request = pipelines.calls[0].request
    assert request.url.params["sha"] == "abc123" and request.url.params["order_by"] == "id"
    assert request.headers["PRIVATE-TOKEN"] == "tok"
    assert status.state == "failure" and status.url == "https://gitlab.com/p/12"
    assert [(r.name, r.status, r.conclusion) for r in status.runs] == [
        ("lint", "completed", "success"),
        ("test", "completed", "failure"),
        ("deploy", "queued", None),
    ]


@respx.mock
async def test_status_is_unknown_without_a_pipeline() -> None:
    respx.get(f"{BASE}/pipelines").mock(return_value=httpx.Response(200, json=[]))
    assert (await adapter().status_for("r", "abc")).state == "unknown"


@respx.mock
async def test_logs_return_the_tail_of_the_trace() -> None:
    trace = "\n".join(f"line {n}" for n in range(1, 11))
    respx.get(f"{BASE}/jobs/77/trace").mock(return_value=httpx.Response(200, text=trace))
    assert await adapter().logs("77", tail=3) == "line 8\nline 9\nline 10"


@respx.mock
async def test_trigger_posts_a_pipeline_and_returns_its_id() -> None:
    route = respx.post(f"{BASE}/pipeline").mock(return_value=httpx.Response(201, json={"id": 4242}))
    pipeline_id = await adapter().trigger("varga/billing-api", "main", "")
    request = route.calls[0].request
    assert request.url.params["ref"] == "main"
    assert request.headers["PRIVATE-TOKEN"] == "tok"
    assert pipeline_id == "4242"


def test_parse_event_delegates_pipeline_payloads() -> None:
    body = (
        b'{"object_kind": "pipeline", "project": {"path_with_namespace": "varga/billing-api"},'
        b' "object_attributes": {"id": 5, "status": "success", "ref": "main", "sha": "abc"}}'
    )
    events = adapter().parse_event({"X-Gitlab-Event-UUID": "u-1"}, body)
    assert [e.type.value for e in events] == ["ci.run.succeeded"]
    assert events[0].delivery_id == "u-1"


def test_the_type_is_registered_with_a_secret_token() -> None:
    specs = {(kind, name): spec for kind, name, spec in connector_types()}
    assert specs[("ci", "gitlab")].secret_fields == ("token",)
