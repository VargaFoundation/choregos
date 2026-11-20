"""The Azure Pipelines CI adapter against a scripted Azure DevOps server, request by request (S13-08).

The server is written from Microsoft's REST API 7.1 reference: these tests prove the adapter speaks that
protocol and reads results as documented, not that a live organisation accepts it.
"""

from __future__ import annotations

import json

import httpx
import pytest
from tranibor_adapters.azure_devops import azure_client
from tranibor_adapters.ci.azure_pipelines import AzurePipelinesCi
from tranibor_adapters.errors import ConfigurationError, UpstreamError
from tranibor_adapters.fakes.azure_devops import FakeAzureDevOpsServer

BASE = "https://dev.azure.com/northwind"
SHA = "a" * 40


def adapter(server: FakeAzureDevOpsServer, **kwargs: object) -> AzurePipelinesCi:
    client = azure_client({"base_url": BASE, "pat": "pat"}, service="azure-pipelines")
    client._client = httpx.AsyncClient(transport=server.transport())
    return AzurePipelinesCi(client, "Billing", **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def server() -> FakeAzureDevOpsServer:
    return FakeAzureDevOpsServer()


async def test_status_reads_the_latest_build_of_each_pipeline_for_the_commit(
    server: FakeAzureDevOpsServer,
) -> None:
    server.add_build(7, SHA, result="failed")
    server.add_build(
        7, SHA, result="succeeded", jobs=[("unit", "completed", "succeeded"), ("lint", "completed", "failed")]
    )
    server.add_build(8, SHA, status="inProgress", result=None)
    server.add_build(7, "b" * 40, result="failed")  # another commit
    status = await adapter(server).status_for("r", SHA)
    assert status.sha == SHA
    assert status.state == "running"  # pipeline 7 is green, 8 still runs
    assert {(r.name, r.status, r.conclusion) for r in status.runs if r.name.startswith("ci")} == {
        ("ci #2026.101", "completed", "success")
    }
    assert [r.name for r in status.runs] == ["release #2026.102", "ci #2026.101"]


async def test_a_failed_build_is_failure_with_its_page(server: FakeAzureDevOpsServer) -> None:
    build_id = server.add_build(7, SHA, result="failed")
    status = await adapter(server).status_for("r", SHA)
    assert status.state == "failure"
    assert status.url == f"https://dev.azure.com/northwind/Billing/_build/results?buildId={build_id}"


@pytest.mark.parametrize(
    ("status", "result", "state"),
    [
        ("completed", "succeeded", "success"),
        ("completed", "failed", "failure"),
        ("completed", "partiallySucceeded", "failure"),
        ("completed", "canceled", "error"),
        ("completed", None, "failure"),  # an unreadable result is not a pass
        ("notStarted", None, "pending"),
        ("postponed", None, "pending"),
        ("inProgress", None, "running"),
        ("cancelling", None, "running"),
    ],
)
async def test_every_build_state_has_a_reading(
    server: FakeAzureDevOpsServer, status: str, result: str | None, state: str
) -> None:
    server.add_build(7, SHA, status=status, result=result)
    assert (await adapter(server).status_for("r", SHA)).state == state


async def test_partially_succeeded_can_be_read_as_neutral(server: FakeAzureDevOpsServer) -> None:
    server.add_build(7, SHA, result="partiallySucceeded")
    assert (await adapter(server, partially_succeeded="neutral").status_for("r", SHA)).state == "success"


async def test_a_commit_without_a_build_is_unknown(server: FakeAzureDevOpsServer) -> None:
    assert (await adapter(server).status_for("r", SHA)).state == "unknown"


async def test_a_pull_request_build_is_found_by_its_source_commit(server: FakeAzureDevOpsServer) -> None:
    server.add_build(7, SHA, pull_request=3, result="succeeded")
    assert (await adapter(server).status_for("r", SHA)).state == "success"


async def test_the_repository_and_pipeline_narrow_the_builds(server: FakeAzureDevOpsServer) -> None:
    server.add_build(7, SHA, result="failed")
    server.add_build(8, SHA, result="succeeded")
    narrowed = adapter(server, repository="billing-api", pipeline="release")
    assert (await narrowed.status_for("r", SHA)).state == "success"
    sent = [c for c in server.calls if c[1].endswith("/build/builds")]
    assert sent and any(path.endswith("/git/repositories/billing-api") for _m, path in server.calls)


async def test_run_status_gives_the_result_and_the_jobs_of_one_run(server: FakeAzureDevOpsServer) -> None:
    run = server.add_build(
        7, SHA, result="failed", jobs=[("unit", "completed", "succeeded"), ("e2e", "completed", "failed")]
    )
    status = await adapter(server).run_status(str(run))
    assert status.state == "failure" and status.sha == SHA
    assert [(r.name, r.conclusion) for r in status.runs[1:]] == [("unit", "success"), ("e2e", "failure")]


async def test_a_run_that_has_not_started_has_no_jobs(server: FakeAzureDevOpsServer) -> None:
    run = server.add_build(7, SHA, status="notStarted", result=None)
    del server.timelines[run]
    status = await adapter(server).run_status(str(run))
    assert status.state == "pending" and len(status.runs) == 1


async def test_logs_are_the_steps_in_order_cut_to_the_tail(server: FakeAzureDevOpsServer) -> None:
    run = server.add_build(7, SHA, log=["a1\na2", "b1\nb2\nb3"])
    ci = adapter(server)
    assert await ci.logs(str(run)) == "a1\na2\nb1\nb2\nb3"
    assert await ci.logs(str(run), tail=2) == "b2\nb3"
    assert ci.logs_url(str(run)) == f"{BASE}/Billing/_build/results?buildId={run}&view=logs"


async def test_queue_sends_the_branch_commit_parameters_and_variables(server: FakeAzureDevOpsServer) -> None:
    ci = adapter(server, pipeline="ci")
    run = await ci.queue(
        "choregos/12-fix", commit=SHA, parameters={"deploy": "false"}, variables={"TICKET": "12"}
    )
    sent = server.runs[0]
    assert sent["pipeline"] == 7 and str(sent["id"]) == run
    assert sent["resources"] == {
        "repositories": {"self": {"refName": "refs/heads/choregos/12-fix", "version": SHA}}
    }
    assert sent["templateParameters"] == {"deploy": "false"}
    assert sent["variables"] == {"TICKET": {"value": "12", "isSecret": False}}


async def test_trigger_queues_a_branch_by_pipeline_name_or_id_or_a_commit(
    server: FakeAzureDevOpsServer,
) -> None:
    ci = adapter(server, pipeline="ci")
    assert (await ci.trigger("r", "main", "")).isdigit()
    await ci.trigger("r", "refs/heads/release", "8")
    await ci.trigger("r", SHA, "Release")  # a commit is run as that commit, on the default branch
    assert [r["pipeline"] for r in server.runs] == [7, 8, 8]
    assert server.runs[1]["resources"]["repositories"]["self"] == {"refName": "refs/heads/release"}
    assert server.runs[2]["resources"]["repositories"]["self"] == {"version": SHA}
    # The queued run is a build that reads as pending.
    assert (await ci.run_status(str(server.runs[0]["id"]))).state == "pending"


async def test_queue_needs_a_pipeline_that_exists(server: FakeAzureDevOpsServer) -> None:
    with pytest.raises(ConfigurationError, match="no `pipeline`"):
        await adapter(server).queue("main")
    with pytest.raises(ConfigurationError, match="no pipeline named"):
        await adapter(server).queue("main", "ghost")


async def test_the_test_call_lists_the_pipelines(server: FakeAzureDevOpsServer) -> None:
    assert await adapter(server).test() == {"ok": True, "project": "Billing"}


async def test_a_refused_token_is_an_upstream_error_not_a_crash() -> None:
    ci = adapter(FakeAzureDevOpsServer(pat="other"))
    with pytest.raises(UpstreamError) as refused:
        await ci.status_for("r", SHA)
    assert refused.value.status_code == 203 and "sign-in" in str(refused.value)


def build_complete(**resource: object) -> bytes:
    return json.dumps(
        {
            "notificationId": 4,
            "eventType": "build.complete",
            "resource": {
                "id": 55,
                "status": "completed",
                "result": "succeeded",
                "sourceBranch": "refs/heads/choregos/12-fix",
                "sourceVersion": SHA,
                "definition": {"id": 7, "name": "ci"},
                "project": {"name": "Billing"},
                "repository": {"name": "billing-api", "project": {"name": "Billing"}},
                "_links": {
                    "web": {"href": "https://dev.azure.com/northwind/Billing/_build/results?buildId=55"}
                },
                **resource,
            },
        }
    ).encode()


def test_build_complete_becomes_a_ci_event_for_the_work_item(server: FakeAzureDevOpsServer) -> None:
    events = adapter(server).parse_event({}, build_complete())
    assert [e.type.value for e in events] == ["ci.run.succeeded"]
    event = events[0]
    assert (event.work_item_key, event.project_slug, event.delivery_id) == ("12", "billing", "4")
    assert event.payload["sha"] == SHA and event.payload["branch"] == "choregos/12-fix"
    assert event.payload["run_id"] == 55 and event.payload["url"].endswith("buildId=55")


@pytest.mark.parametrize(
    ("result", "kind"),
    [("failed", "ci.run.failed"), ("canceled", "ci.run.failed"), ("partiallySucceeded", "ci.run.failed")],
)
def test_a_build_that_did_not_succeed_is_never_a_success(
    server: FakeAzureDevOpsServer, result: str, kind: str
) -> None:
    assert [e.type.value for e in adapter(server).parse_event({}, build_complete(result=result))] == [kind]


def test_a_build_with_no_result_and_unknown_events_are_ignored(server: FakeAzureDevOpsServer) -> None:
    assert adapter(server).parse_event({}, build_complete(result="")) == []
    assert adapter(server).parse_event({}, b'{"eventType": "git.push", "resource": {}}') == []
    assert adapter(server).parse_event({}, b"{}") == []


def run_state(state: str, result: str | None = None) -> bytes:
    return json.dumps(
        {
            "notificationId": 9,
            "eventType": "ms.vss-pipelines.run-state-changed-event",
            "resource": {
                "run": {
                    "id": 61,
                    "state": state,
                    **({"result": result} if result else {}),
                    "pipeline": {"id": 7},
                    "_links": {
                        "web": {"href": "https://dev.azure.com/northwind/Billing/_build/results?buildId=61"}
                    },
                },
                "project": {"name": "Billing"},
                "repositories": [{"refName": "refs/heads/choregos/12-fix", "version": SHA}],
            },
        }
    ).encode()


def test_run_state_changes_become_started_and_finished_events(server: FakeAzureDevOpsServer) -> None:
    ci = adapter(server)
    assert [e.type.value for e in ci.parse_event({}, run_state("inProgress"))] == ["ci.run.started"]
    assert [e.type.value for e in ci.parse_event({}, run_state("completed", "succeeded"))] == [
        "ci.run.succeeded"
    ]
    failed = ci.parse_event({}, run_state("completed", "failed"))
    assert failed[0].type.value == "ci.run.failed" and failed[0].payload["sha"] == SHA
    assert failed[0].work_item_key == "12" and failed[0].payload["run_id"] == 61
    assert ci.parse_event({}, run_state("canceling")) == []
