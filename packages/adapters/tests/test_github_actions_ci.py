"""The GitHub Actions CI adapter against the REST API's documented shapes (simulated transport)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from tranibor_adapters import connector_types
from tranibor_adapters.ci.github_actions import GitHubActionsCi
from tranibor_adapters.errors import ConfigurationError, UpstreamError
from tranibor_adapters.github import APP_EVENTS, APP_PERMISSIONS
from tranibor_adapters.github.client import GitHubClient

from ._transport import Wire

API = "https://api.github.test"
REPO = "northwind/billing"
SHA = "a" * 40
RUNS = f"/repos/{REPO}/actions/workflows/ci.yml/runs"


def ci(wire: Wire, **kwargs: Any) -> GitHubActionsCi:
    async def no_sleep(_: float) -> None:
        return None

    client = GitHubClient(token="t", base_url=API, client=wire.client())
    return GitHubActionsCi(client, REPO, workflow="ci.yml", sleep=no_sleep, **kwargs)


def run(
    run_id: int,
    status: str,
    conclusion: str | None,
    *,
    workflow_id: int = 1,
    name: str = "CI",
    at: str = "2026-11-20T10:00:00Z",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": name,
        "workflow_id": workflow_id,
        "path": ".github/workflows/ci.yml" if workflow_id == 1 else ".github/workflows/lint.yml",
        "status": status,
        "conclusion": conclusion,
        "created_at": at,
        "head_sha": SHA,
        "html_url": f"https://github.test/{REPO}/actions/runs/{run_id}",
    }


def job(name: str, status: str, conclusion: str | None, job_id: int = 1) -> dict[str, Any]:
    return {
        "id": job_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.test/j/{job_id}",
    }


async def test_trigger_dispatches_with_inputs_and_finds_the_new_run() -> None:
    wire = Wire(
        {
            ("GET", RUNS): [
                (200, {"workflow_runs": [run(10, "completed", "success")]}),
                (200, {"workflow_runs": [run(10, "completed", "success")]}),  # not visible yet
                (
                    200,
                    {
                        "workflow_runs": [
                            run(12, "queued", None),
                            run(11, "queued", None),
                            run(10, "completed", "success"),
                        ]
                    },
                ),
            ],
            ("POST", f"/repos/{REPO}/actions/workflows/ci.yml/dispatches"): (204, None),
        }
    )
    run_id = await ci(wire, inputs={"env": "ci"}).trigger(REPO, "choregos/12-fix", "")
    assert run_id == "11"  # the oldest run that did not exist before the dispatch
    assert wire.body(1) == {"ref": "choregos/12-fix", "inputs": {"env": "ci"}}
    first = wire.requests[0].url.params
    assert first["event"] == "workflow_dispatch" and first["branch"] == "choregos/12-fix"
    assert wire.requests[1].headers["Authorization"] == "Bearer t"


async def test_a_dispatch_that_starts_no_run_is_an_error_not_a_guess() -> None:
    wire = Wire(
        {
            ("GET", RUNS): (200, {"workflow_runs": [run(10, "completed", "success")]}),
            ("POST", f"/repos/{REPO}/actions/workflows/ci.yml/dispatches"): (204, None),
        }
    )
    with pytest.raises(UpstreamError, match="no run"):
        await ci(wire, poll_attempts=3).trigger(REPO, "main", "")


async def test_a_refused_dispatch_is_the_githubs_error() -> None:
    wire = Wire(
        {
            ("GET", RUNS): (200, {"workflow_runs": []}),
            ("POST", f"/repos/{REPO}/actions/workflows/ci.yml/dispatches"): (
                422,
                {"message": "no workflow_dispatch"},
            ),
        }
    )
    with pytest.raises(UpstreamError) as refused:
        await ci(wire).trigger(REPO, "main", "")
    assert refused.value.status_code == 422


async def test_a_workflow_is_required_to_trigger() -> None:
    wire = Wire()
    client = GitHubClient(token="t", base_url=API, client=wire.client())
    with pytest.raises(ConfigurationError):
        await GitHubActionsCi(client, REPO).trigger(REPO, "main", "")
    with pytest.raises(ConfigurationError):
        GitHubActionsCi(client, "noslash")


async def test_a_pipeline_argument_names_another_workflow() -> None:
    other = f"/repos/{REPO}/actions/workflows/release.yml"
    wire = Wire(
        {
            ("GET", f"{other}/runs"): [
                (200, {"workflow_runs": []}),
                (200, {"workflow_runs": [run(5, "queued", None)]}),
            ],
            ("POST", f"{other}/dispatches"): (204, None),
        }
    )
    assert await ci(wire).trigger(REPO, "main", "release.yml") == "5"
    assert "inputs" not in wire.body(1)


async def test_status_judges_the_newest_run_of_each_workflow_and_lists_the_jobs() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/actions/runs"): (
                200,
                {
                    "workflow_runs": [
                        run(22, "completed", "success", at="2026-11-20T11:00:00Z"),
                        run(21, "completed", "failure", at="2026-11-20T10:00:00Z"),  # replaced by a rerun
                        run(30, "completed", "failure", workflow_id=2, name="Lint"),
                    ]
                },
            ),
            ("GET", f"/repos/{REPO}/actions/runs/22/jobs"): (
                200,
                {"jobs": [job("test", "completed", "success", 1)]},
            ),
            ("GET", f"/repos/{REPO}/actions/runs/30/jobs"): (
                200,
                {"jobs": [job("ruff", "completed", "failure", 2)]},
            ),
        }
    )
    status = await GitHubActionsCi(
        GitHubClient(token="t", base_url=API, client=wire.client()), REPO
    ).status_for(REPO, SHA)
    assert wire.requests[0].url.params["head_sha"] == SHA
    assert status.state == "failure"
    assert [(r.name, r.conclusion) for r in status.runs] == [
        ("CI / test", "success"),
        ("Lint / ruff", "failure"),
    ]


@pytest.mark.parametrize(
    ("status", "conclusion", "state"),
    [
        ("completed", "success", "success"),
        ("completed", "skipped", "success"),
        ("completed", "neutral", "success"),
        ("completed", "failure", "failure"),
        ("completed", "timed_out", "failure"),
        ("completed", "startup_failure", "failure"),
        ("completed", "cancelled", "error"),
        ("completed", "action_required", "pending"),
        ("in_progress", None, "running"),
        ("queued", None, "pending"),
    ],
)
async def test_every_run_status_and_conclusion_has_a_state(
    status: str, conclusion: str | None, state: str
) -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/actions/runs"): (200, {"workflow_runs": [run(1, status, conclusion)]}),
            ("GET", f"/repos/{REPO}/actions/runs/1/jobs"): (200, {"jobs": []}),
        }
    )
    assert (await ci(wire).status_for(REPO, SHA)).state == state


async def test_a_running_workflow_keeps_the_commit_running_even_when_another_passed() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/actions/runs"): (
                200,
                {
                    "workflow_runs": [
                        run(1, "completed", "success"),
                        run(2, "in_progress", None, workflow_id=2, name="Lint"),
                    ]
                },
            ),
            ("GET", f"/repos/{REPO}/actions/runs/1/jobs"): (200, {"jobs": []}),
            ("GET", f"/repos/{REPO}/actions/runs/2/jobs"): (200, {"jobs": []}),
        }
    )
    status = await GitHubActionsCi(
        GitHubClient(token="t", base_url=API, client=wire.client()), REPO
    ).status_for(REPO, SHA)
    assert status.state == "running"


async def test_the_configured_workflow_alone_is_read() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/actions/runs"): (
                200,
                {
                    "workflow_runs": [
                        run(1, "completed", "success"),
                        run(2, "completed", "failure", workflow_id=2, name="Lint"),
                    ]
                },
            ),
            ("GET", f"/repos/{REPO}/actions/runs/1/jobs"): (200, {"jobs": []}),
        }
    )
    assert (await ci(wire).status_for(REPO, SHA)).state == "success"


async def test_status_is_unknown_without_a_run() -> None:
    wire = Wire({("GET", f"/repos/{REPO}/actions/runs"): (200, {"workflow_runs": []})})
    assert (await ci(wire).status_for(REPO, SHA)).state == "unknown"


async def test_run_status_reads_one_run_and_its_jobs() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/actions/runs/7"): (200, run(7, "completed", "failure")),
            ("GET", f"/repos/{REPO}/actions/runs/7/jobs"): (
                200,
                {"jobs": [job("build", "completed", "success", 1), job("test", "completed", "failure", 2)]},
            ),
        }
    )
    status = await ci(wire).run_status("7")
    assert status.state == "failure" and status.sha == SHA
    assert [(r.name, r.conclusion) for r in status.runs] == [("build", "success"), ("test", "failure")]


async def test_rerun_failed_and_cancel_post_to_the_run() -> None:
    wire = Wire(
        {
            ("POST", f"/repos/{REPO}/actions/runs/7/rerun-failed-jobs"): (201, None),
            ("POST", "/repos/other/app/actions/runs/8/cancel"): (202, None),
        }
    )
    await ci(wire).rerun_failed("7")
    await ci(wire).cancel("other/app@8")
    assert wire.sent("POST") == [
        ("POST", f"/repos/{REPO}/actions/runs/7/rerun-failed-jobs"),
        ("POST", "/repos/other/app/actions/runs/8/cancel"),
    ]


async def test_logs_follow_the_redirect_without_the_token_and_cut_the_tail() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [job("test", "completed", "failure", 99)]})
        if request.url.path.endswith("/jobs/99/logs"):
            return httpx.Response(302, headers={"location": "https://storage.example.com/log?sig=1"})
        return httpx.Response(200, text="\n".join(f"l{n}" for n in range(1, 6)))

    client = GitHubClient(
        token="t", base_url=API, client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    text = await GitHubActionsCi(client, REPO).logs("7", tail=2)
    assert text == "== test\nl4\nl5"
    assert seen[-1].url.host == "storage.example.com" and "authorization" not in seen[-1].headers
    assert seen[1].headers["authorization"] == "Bearer t"


def test_a_workflow_run_webhook_becomes_a_ci_event() -> None:
    payload = {
        "action": "completed",
        "repository": {"full_name": REPO},
        "sender": {"login": "octo"},
        "workflow_run": {
            "id": 7,
            "run_attempt": 2,
            "name": "CI",
            "conclusion": "failure",
            "head_sha": SHA,
            "head_branch": "choregos/12-fix",
            "event": "workflow_dispatch",
            "html_url": "https://github.test/run/7",
            "pull_requests": [{"number": 3}],
        },
    }
    headers = {"X-GitHub-Event": "workflow_run", "X-GitHub-Delivery": "d-1"}
    (event,) = ci(Wire()).parse_event(headers, json.dumps(payload).encode())
    assert event.type.value == "ci.run.failed" and event.delivery_id == "d-1"
    assert (event.project_slug, event.work_item_key) == ("billing", f"{REPO}#12")
    assert event.payload["run_attempt"] == 2 and event.payload["pr_numbers"] == [3]
    for conclusion, kind in (
        ("success", "ci.run.succeeded"),
        ("skipped", "ci.run.succeeded"),
        ("cancelled", "ci.run.failed"),
    ):
        payload["workflow_run"]["conclusion"] = conclusion  # type: ignore[index]
        assert ci(Wire()).parse_event(headers, json.dumps(payload).encode())[0].type.value == kind
    for action, kind in (("requested", "ci.run.started"), ("in_progress", "ci.run.started")):
        payload["action"] = action
        assert ci(Wire()).parse_event(headers, json.dumps(payload).encode())[0].type.value == kind
    payload["action"] = "unknown"
    assert ci(Wire()).parse_event(headers, json.dumps(payload).encode()) == []


async def test_the_connection_test_reads_the_workflow() -> None:
    wire = Wire({("GET", f"/repos/{REPO}/actions/workflows/ci.yml"): (200, {"id": 1})})
    assert await ci(wire).test() == {"ok": True, "repo": REPO, "workflow": "ci.yml"}


def test_the_app_is_asked_for_what_this_connector_needs() -> None:
    assert APP_PERMISSIONS["actions"] == "write" and "workflow_run" in APP_EVENTS


def test_the_type_is_registered_and_its_egress_is_the_github_api() -> None:
    specs = {(kind, name): spec for kind, name, spec in connector_types()}
    spec = specs[("ci", "github-actions")]
    assert spec.secret_fields == () and spec.config_schema["required"] == ["repo"]
