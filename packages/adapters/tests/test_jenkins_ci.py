"""The Jenkins CI adapter against the documented remote API, request by request (respx mocks), and
against a simulated controller whose queue turns into a build."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from tranibor_adapters import connector_types
from tranibor_adapters.ci.jenkins import JenkinsCi, job_path, parse_jenkins_event
from tranibor_adapters.errors import ConfigurationError, UpstreamError
from tranibor_adapters.http import RestClient

BASE = "https://jenkins.example.com"
SHA = "a" * 40
CRUMB = {"crumbRequestField": "Jenkins-Crumb", "crumb": "c-1"}


def adapter(job: str = "team/app", **kwargs: object) -> JenkinsCi:
    async def no_sleep(_: float) -> None:
        return None

    client = RestClient(BASE, auth=("bot", "api-token"), service="jenkins")
    return JenkinsCi(client, job, sleep=no_sleep, **kwargs)  # type: ignore[arg-type]


def test_a_job_path_is_one_segment_per_folder_and_a_branch_is_encoded() -> None:
    assert job_path("team/app") == "/job/team/job/app"
    assert job_path("/team/app/") == "/job/team/job/app"
    assert job_path("app", "feature/x y") == "/job/app/job/feature%2Fx%20y"
    assert job_path("app", "refs/heads/main") == "/job/app/job/main"


def test_a_job_is_required() -> None:
    with pytest.raises(ConfigurationError):
        adapter("")


@respx.mock
async def test_trigger_asks_a_crumb_then_queues_the_build_with_parameters() -> None:
    respx.get(f"{BASE}/crumbIssuer/api/json").mock(return_value=httpx.Response(200, json=CRUMB))
    queued = respx.post(f"{BASE}/job/team/job/app/buildWithParameters").mock(
        return_value=httpx.Response(201, headers={"Location": f"{BASE}/queue/item/731/"})
    )
    run = await adapter(parameters={"ENV": "ci"}).trigger("northwind/app", "choregos/12-fix", "")
    request = queued.calls[0].request
    assert run == "queue:731"
    assert request.url.params["BRANCH"] == "choregos/12-fix" and request.url.params["ENV"] == "ci"
    assert request.headers["Jenkins-Crumb"] == "c-1"
    assert request.headers["Authorization"].startswith("Basic ")  # the user and its API token


@respx.mock
async def test_a_controller_without_a_crumb_issuer_is_triggered_without_one() -> None:
    respx.get(f"{BASE}/crumbIssuer/api/json").mock(return_value=httpx.Response(404))
    queued = respx.post(f"{BASE}/job/team/job/app/buildWithParameters").mock(
        return_value=httpx.Response(201, headers={"Location": f"{BASE}/queue/item/5/"})
    )
    assert await adapter().trigger("r", "main", "") == "queue:5"
    assert "Jenkins-Crumb" not in queued.calls[0].request.headers


@respx.mock
async def test_the_crumb_can_be_switched_off() -> None:
    crumb = respx.get(f"{BASE}/crumbIssuer/api/json").mock(return_value=httpx.Response(200, json=CRUMB))
    respx.post(f"{BASE}/job/team/job/app/buildWithParameters").mock(
        return_value=httpx.Response(201, headers={"Location": f"{BASE}/queue/item/5/"})
    )
    await adapter(crumb=False).trigger("r", "main", "")
    assert not crumb.called


@respx.mock
async def test_a_crumb_failure_other_than_absence_is_not_hidden() -> None:
    respx.get(f"{BASE}/crumbIssuer/api/json").mock(return_value=httpx.Response(401, text="no"))
    with pytest.raises(UpstreamError) as refused:
        await adapter().trigger("r", "main", "")
    assert refused.value.status_code == 401


@respx.mock
async def test_a_multibranch_job_is_built_on_the_branch_and_sends_no_ref_parameter() -> None:
    respx.get(f"{BASE}/crumbIssuer/api/json").mock(return_value=httpx.Response(404))
    queued = respx.post(f"{BASE}/job/app/job/choregos%2F12-fix/build").mock(
        return_value=httpx.Response(201, headers={"Location": f"{BASE}/queue/item/9/"})
    )
    run = await adapter("app", multibranch=True).trigger("r", "choregos/12-fix", "")
    assert run == "queue:9" and not dict(queued.calls[0].request.url.params)


@respx.mock
async def test_an_answer_without_a_queue_location_is_an_error() -> None:
    respx.get(f"{BASE}/crumbIssuer/api/json").mock(return_value=httpx.Response(404))
    respx.post(f"{BASE}/job/team/job/app/buildWithParameters").mock(return_value=httpx.Response(201))
    with pytest.raises(UpstreamError):
        await adapter().trigger("r", "main", "")


@respx.mock
async def test_the_queue_item_is_followed_to_its_build() -> None:
    item = respx.get(f"{BASE}/queue/item/731/api/json").mock(
        side_effect=[
            httpx.Response(200, json={"why": "waiting for an executor"}),
            httpx.Response(200, json={"executable": {"number": 7, "url": f"{BASE}/job/team/job/app/7/"}}),
        ]
    )
    build = await adapter().wait_for_build("queue:731")
    assert item.call_count == 2
    assert (build.number, build.path) == (7, "/job/team/job/app/7")


@respx.mock
async def test_a_queue_that_never_starts_is_left_queued_after_the_attempts() -> None:
    respx.get(f"{BASE}/queue/item/1/api/json").mock(return_value=httpx.Response(200, json={}))
    build = await adapter(poll_attempts=3).wait_for_build("queue:1")
    assert build.queued and build.path is None


@respx.mock
async def test_a_cancelled_or_forgotten_queue_item_is_cancelled() -> None:
    respx.get(f"{BASE}/queue/item/2/api/json").mock(
        return_value=httpx.Response(200, json={"cancelled": True})
    )
    respx.get(f"{BASE}/queue/item/3/api/json").mock(return_value=httpx.Response(404))
    assert (await adapter().build_of("queue:2")).cancelled
    assert (await adapter().build_of("queue:3")).cancelled


@respx.mock
async def test_the_build_status_reads_the_result_and_the_console_url() -> None:
    respx.get(f"{BASE}/queue/item/731/api/json").mock(
        return_value=httpx.Response(
            200, json={"executable": {"number": 7, "url": f"{BASE}/job/team/job/app/7/"}}
        )
    )
    respx.get(f"{BASE}/job/team/job/app/7/api/json").mock(
        return_value=httpx.Response(
            200, json={"number": 7, "url": f"{BASE}/job/team/job/app/7/", "result": "FAILURE"}
        )
    )
    status = await adapter().build_status("queue:731")
    assert status.state == "failure"
    assert status.url == f"{BASE}/job/team/job/app/7/console"
    assert [(r.status, r.conclusion) for r in status.runs] == [("completed", "failure")]


@pytest.mark.parametrize(
    ("build", "state", "conclusion"),
    [
        ({"result": "SUCCESS"}, "success", "success"),
        ({"result": "UNSTABLE"}, "failure", "failure"),
        ({"result": "ABORTED"}, "error", "cancelled"),
        ({"result": "NOT_BUILT"}, "error", "skipped"),
        ({"result": None, "building": True}, "running", None),
        ({"result": None, "building": False}, "pending", None),
    ],
)
@respx.mock
async def test_every_jenkins_result_has_a_state(
    build: dict[str, object], state: str, conclusion: str | None
) -> None:
    respx.get(f"{BASE}/queue/item/1/api/json").mock(
        return_value=httpx.Response(
            200, json={"executable": {"number": 1, "url": f"{BASE}/job/team/job/app/1/"}}
        )
    )
    respx.get(f"{BASE}/job/team/job/app/1/api/json").mock(return_value=httpx.Response(200, json=build))
    status = await adapter().build_status("queue:1")
    assert status.state == state and status.runs[0].conclusion == conclusion


@respx.mock
async def test_the_console_tail_is_returned_and_a_queued_build_has_none() -> None:
    text = "\n".join(f"line {n}" for n in range(1, 11))
    respx.get(f"{BASE}/queue/item/4/api/json").mock(
        return_value=httpx.Response(
            200, json={"executable": {"number": 3, "url": f"{BASE}/job/team/job/app/3/"}}
        )
    )
    respx.get(f"{BASE}/queue/item/5/api/json").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{BASE}/job/team/job/app/3/consoleText").mock(return_value=httpx.Response(200, text=text))
    assert await adapter().logs("queue:4", tail=2) == "line 9\nline 10"
    assert await adapter().logs("queue:5") == ""


@respx.mock
async def test_a_controller_behind_a_context_path_keeps_it_out_of_the_build_path() -> None:
    client = RestClient("https://ci.example.com/jenkins", auth=("bot", "t"), service="jenkins")
    respx.get("https://ci.example.com/jenkins/queue/item/8/api/json").mock(
        return_value=httpx.Response(
            200, json={"executable": {"number": 2, "url": "https://ci.example.com/jenkins/job/app/2/"}}
        )
    )
    log = respx.get("https://ci.example.com/jenkins/job/app/2/consoleText").mock(
        return_value=httpx.Response(200, text="ok")
    )
    assert await JenkinsCi(client, "app").logs("queue:8") == "ok" and log.called


def _build(number: int, result: str | None, sha: str | None, *, building: bool = False) -> dict[str, object]:
    return {
        "number": number,
        "url": f"{BASE}/job/team/job/app/{number}/",
        "result": result,
        "building": building,
        "actions": [{}, {"lastBuiltRevision": {"SHA1": sha}}] if sha else [{}],
    }


@respx.mock
async def test_status_for_takes_the_newest_build_of_the_commit() -> None:
    route = respx.get(f"{BASE}/job/team/job/app/api/json").mock(
        return_value=httpx.Response(
            200,
            json={
                "builds": [
                    _build(9, None, "f" * 40),
                    _build(8, "SUCCESS", SHA),
                    _build(7, "FAILURE", SHA),
                ]
            },
        )
    )
    status = await adapter().status_for("northwind/app", SHA)
    assert "builds[" in route.calls[0].request.url.params["tree"]
    assert status.state == "success" and status.url == f"{BASE}/job/team/job/app/8/console"
    assert status.runs[0].name == "team/app"


@respx.mock
async def test_status_for_reads_a_commit_passed_as_a_parameter() -> None:
    build = _build(4, None, None, building=True)
    build["actions"] = [{"parameters": [{"name": "SHA", "value": SHA}]}]
    respx.get(f"{BASE}/job/team/job/app/api/json").mock(
        return_value=httpx.Response(200, json={"builds": [build]})
    )
    assert (await adapter().status_for("r", SHA)).state == "running"


@respx.mock
async def test_status_is_unknown_when_no_recent_build_names_the_commit() -> None:
    respx.get(f"{BASE}/job/team/job/app/api/json").mock(
        return_value=httpx.Response(200, json={"builds": [_build(1, "SUCCESS", "e" * 40)]})
    )
    assert (await adapter().status_for("r", SHA)).state == "unknown"


@respx.mock
async def test_status_for_a_multibranch_job_looks_through_its_branches() -> None:
    respx.get(f"{BASE}/job/app/api/json").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {"name": "main", "builds": [_build(3, "SUCCESS", "e" * 40)]},
                    {"name": "choregos%2F12-fix", "builds": [_build(1, "FAILURE", SHA)]},
                ]
            },
        )
    )
    status = await adapter("app", multibranch=True).status_for("r", SHA)
    assert status.state == "failure" and status.runs[0].name == "app/choregos%2F12-fix"


@respx.mock
async def test_the_connection_test_reads_the_job() -> None:
    respx.get(f"{BASE}/job/team/job/app/api/json").mock(
        return_value=httpx.Response(200, json={"name": "app"})
    )
    assert await adapter().test() == {"ok": True, "job": "team/app"}


def test_a_notification_plugin_payload_becomes_a_ci_event() -> None:
    base = {
        "name": "app",
        "build": {
            "number": 7,
            "phase": "COMPLETED",
            "status": "SUCCESS",
            "full_url": f"{BASE}/job/app/7/",
            "parameters": {},
            "scm": {
                "url": "https://github.com/northwind/app.git",
                "branch": "origin/choregos/12-fix",
                "commit": SHA,
            },
        },
    }
    (done,) = parse_jenkins_event(base)
    assert done.type.value == "ci.run.succeeded" and done.source == "jenkins"
    assert done.project_slug == "app" and done.work_item_key == "northwind/app#12"
    assert done.payload["sha"] == SHA and done.delivery_id == f"jenkins:{BASE}/job/app/7:ci.run.succeeded"
    base["build"] = {**base["build"], "status": "FAILURE"}  # type: ignore[dict-item]
    assert parse_jenkins_event(base)[0].type.value == "ci.run.failed"
    base["build"] = {**base["build"], "phase": "STARTED", "status": ""}  # type: ignore[dict-item]
    assert parse_jenkins_event(base)[0].type.value == "ci.run.started"
    for ignored in ("QUEUED", "FINALIZED"):
        base["build"] = {**base["build"], "phase": ignored}  # type: ignore[dict-item]
        assert parse_jenkins_event(base) == []


def test_the_same_job_and_build_number_on_two_controllers_do_not_share_a_delivery_id() -> None:
    def event(base: str, **build: Any) -> Any:
        payload = {
            "name": "app",
            "build": {"number": 7, "phase": "STARTED", "full_url": f"{base}/job/app/7/", **build},
        }
        return parse_jenkins_event(payload)[0]

    a, b = event("https://jenkins.a.example"), event("https://jenkins.b.example")
    assert a.delivery_id != b.delivery_id
    assert a.delivery_id == event("https://jenkins.a.example").delivery_id, "a redelivery keeps its id"
    flat = {"job": "app", "build_number": 7, "url": "https://jenkins.b.example/job/app/7/"}
    (flat_event,) = adapter().parse_event({}, json.dumps(flat).encode())
    assert flat_event.delivery_id == "jenkins:https://jenkins.b.example/job/app/7:ci.run.started"


def test_the_flat_payload_names_its_project_and_work_item() -> None:
    body = (
        b'{"job": "team/app", "build_number": 3, "result": "UNSTABLE", "sha": "abc",'
        b' "project": "billing-api", "work_item": "northwind/app#4"}'
    )
    (event,) = adapter().parse_event({}, body)
    assert event.type.value == "ci.run.failed"
    assert (event.project_slug, event.work_item_key) == ("billing-api", "northwind/app#4")


def test_a_build_parameter_names_the_work_item_for_the_notification_plugin() -> None:
    payload = {
        "name": "app",
        "build": {
            "number": 1,
            "phase": "STARTED",
            "parameters": {"CHOREGOS_PROJECT": "billing-api", "CHOREGOS_WORK_ITEM": "k-1"},
        },
    }
    (event,) = parse_jenkins_event(payload)
    assert (event.project_slug, event.work_item_key) == ("billing-api", "k-1")


def test_the_type_is_registered_with_a_secret_token_and_an_egress() -> None:
    specs = {(kind, name): spec for kind, name, spec in connector_types()}
    spec = specs[("ci", "jenkins")]
    assert spec.secret_fields == ("token",)
    assert spec.config_schema["required"] == ["base_url", "job", "user"]


def test_the_factory_builds_the_adapter_from_a_configuration() -> None:
    from tranibor_adapters import registry

    built = registry._REGISTRY[("ci", "jenkins")](
        {"base_url": BASE, "job": "team/app", "user": "bot", "token": "t", "multibranch": True}
    )
    assert isinstance(built, JenkinsCi) and built.multibranch
