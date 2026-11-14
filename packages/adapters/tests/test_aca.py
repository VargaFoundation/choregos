"""Executor Azure Container Apps : idempotence, secret de run, statut, annulation, logs.

As for Jira and GitLab, the tests speak the documented ARM protocol through a simulated
transport: they prove the shape of the calls, not that an Azure subscription accepts them.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import httpx
import pytest
from choregos_adapters.errors import ConfigurationError, UpstreamError
from choregos_adapters.executor.aca import AcaExecutor, AzureArmClient
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, StageJobSpec

SUB = "00000000-0000-0000-0000-000000000000"
RG = "rg-choregos"
ENV = f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.App/managedEnvironments/env"
JOBS = f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.App/jobs"


def executor(handler: Any, **kwargs: Any) -> AcaExecutor:
    client = AzureArmClient(
        subscription_id=SUB,
        resource_group=RG,
        token="jeton-arm",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return AcaExecutor(client, environment_id=ENV, **kwargs)


def spec(run_id: str = "run-42") -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="billing-api",
        namespace=RG,
        runner_image="registry.example/choregos-runner@sha256:abc",
        api_url="https://api.choregos.dev/api/v1/internal",
        run_token="jeton-du-run",
        timeout_minutes=90,
        env={"CHOREGOS_BACKEND": "claude-code"},
        labels={"choregos/env": "prod"},
    )


def test_a_missing_environment_is_refused() -> None:
    with pytest.raises(ConfigurationError):
        AcaExecutor(AzureArmClient(token="x"), environment_id="")


async def test_the_job_is_created_then_triggered() -> None:
    calls: list[tuple[str, str]] = []
    definition: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer jeton-arm"
        assert request.url.params["api-version"] == "2024-03-01"
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404, json={"error": "NotFound"})
        if request.method == "PUT":
            definition.update(jsonlib.loads(request.content))
            return httpx.Response(201, json=definition)
        return httpx.Response(200, json={"name": "run-42-xyz"})

    ref = await executor(handler).start(spec())

    assert ref.kind is ExecutorKind.ACA
    assert ref.name == "run-run-42"
    assert ("PUT", f"{JOBS}/run-run-42") in calls
    assert ("POST", f"{JOBS}/run-run-42/start") in calls

    properties = definition["properties"]
    assert properties["environmentId"] == ENV
    assert properties["configuration"]["triggerType"] == "Manual"
    assert properties["configuration"]["replicaTimeout"] == 90 * 60
    assert properties["configuration"]["replicaRetryLimit"] == 0, "the retry is the orchestrator's"


async def test_the_runs_token_goes_through_a_secret() -> None:
    definition: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404, json={})
        if request.method == "PUT":
            definition.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    await executor(handler).start(spec())

    container = definition["properties"]["template"]["containers"][0]
    token_env = next(e for e in container["env"] if e["name"] == "CHOREGOS_RUN_TOKEN")
    assert token_env == {"name": "CHOREGOS_RUN_TOKEN", "secretRef": "run-token"}
    assert not any("value" in e and e["value"] == "jeton-du-run" for e in container["env"])
    secrets = definition["properties"]["configuration"]["secrets"]
    assert secrets == [{"name": "run-token", "value": "jeton-du-run"}]


async def test_restarting_a_run_does_not_play_it_twice() -> None:
    """Replaying the `start_run` activity must not trigger a second execution."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": [{"name": "run-42-xyz", "properties": {}}]})
        return httpx.Response(200, json={"name": "run-run-42"})

    await executor(handler).start(spec())

    assert not any(path.endswith("/start") for _, path in calls), "no second trigger"


async def test_the_identity_and_the_registry_are_set_only_if_configured() -> None:
    definitions: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404, json={})
        if request.method == "PUT":
            definitions.append(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    await executor(handler).start(spec("run-1"))
    identity = "/subscriptions/x/resourcegroups/rg/providers/Microsoft.ManagedIdentity/u/id"
    await executor(handler, identity_id=identity, registry_server="acme.azurecr.io").start(spec("run-2"))

    assert "identity" not in definitions[0]
    assert definitions[1]["identity"]["type"] == "UserAssigned"
    assert definitions[1]["properties"]["configuration"]["registries"][0]["identity"] == identity


@pytest.mark.parametrize(
    ("azure", "expected"),
    [
        ("Succeeded", "succeeded"),
        ("Failed", "failed"),
        ("Running", "running"),
        ("Processing", "running"),
        ("Stopped", "cancelled"),
        ("Inconnu", "pending"),
    ],
)
async def test_status_translation(azure: str, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "name": "run-42-xyz",
                        "properties": {
                            "status": azure,
                            "startTime": "2026-09-19T08:00:00Z",
                            "endTime": "2026-09-19T08:12:00Z",
                        },
                    }
                ]
            },
        )

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    status = await executor(handler).status(ref)

    assert status.state == expected
    assert status.started_at is not None and status.ended_at is not None


async def test_a_job_without_an_execution_is_unknown_not_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": []})

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    status = await executor(handler).status(ref)
    assert status.state == "unknown"


async def test_cancellation_stops_the_running_execution() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": [{"name": "run-42-xyz", "properties": {}}]})
        return httpx.Response(200, json={})

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    await executor(handler).cancel(ref)

    assert ("POST", f"{JOBS}/run-run-42/executions/run-42-xyz/stop") in calls


async def test_without_log_analytics_the_logs_say_where_to_look() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    lines = [line async for line in executor(handler).logs(ref)]

    assert len(lines) == 1
    assert "ContainerAppConsoleLogs_CL" in lines[0], "the lead is given, not silence"


async def test_with_log_analytics_the_lines_arrive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.loganalytics.io" in str(request.url)
        assert "ContainerAppConsoleLogs_CL" in jsonlib.loads(request.content)["query"]
        return httpx.Response(
            200,
            json={
                "tables": [{"rows": [["2026-09-19T08:00:00Z", "runner: starting"], ["…", "runner: fini"]]}]
            },
        )

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    lines = [line async for line in executor(handler, log_analytics_workspace_id="ws-1").logs(ref)]

    assert lines == ["runner: starting", "runner: fini"]


async def test_an_arm_error_is_named() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="AuthorizationFailed")

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    with pytest.raises(UpstreamError) as error:
        await executor(handler).status(ref)
    assert "azure-aca" in str(error.value)
    assert error.value.status_code == 403


async def test_the_aad_token_is_requested_then_cached() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "jeton-frais", "expires_in": 3600})
        return httpx.Response(200, json={"value": []})

    client = AzureArmClient(
        subscription_id=SUB,
        resource_group=RG,
        tenant_id="t",
        client_id="c",
        client_secret="s",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    aca = AcaExecutor(client, environment_id=ENV)
    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    await aca.status(ref)
    await aca.status(ref)

    assert sum(1 for url in calls if "oauth2" in url) == 1, "the token is asked for only once"


async def test_an_incomplete_identity_is_refused_clearly() -> None:
    client = AzureArmClient(subscription_id=SUB, resource_group=RG)
    with pytest.raises(ConfigurationError) as error:
        await client.token()
    assert "tenant_id" in str(error.value)


async def test_no_tag_name_carries_a_character_refused_by_arm() -> None:
    """The regression that cost S13-05: ARM refuses `< > % & \\ ? /` in a tag *name*.

    Our labels are written Kubernetes-style (`choregos/run-id`), exactly the forbidden
    shape. The simulated transport does not validate names, so the eighteen tests next to it
    passed while no job could have been created. This test redoes the validation here.
    """
    sent_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "PUT":
            sent_body.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    aca = executor(handler)
    job = spec()
    # Caller labels in the k8s style: they too must be translated.
    job.labels = {
        "app.kubernetes.io/name": "choregos",
        "choregos/stage": "implement",
        "carefree": "ok",
    }
    await aca.start(job)

    forbidden = set("<>%&\\?/")
    for tag_name in sent_body.get("tags", {}):
        assert not (set(tag_name) & forbidden), f"tag name refused by ARM: {tag_name!r}"

    tags = sent_body["tags"]
    # The translation keeps the meaning: a `/` becomes `_`, the rest is intact.
    assert tags["choregos_run-id"] == job.run_id
    assert tags["choregos_project"] == job.project_slug
    assert tags["app.kubernetes.io_name"] == "choregos"
    assert tags["choregos_stage"] == "implement"
    assert tags["carefree"] == "ok"


async def test_the_steps_resources_are_honoured() -> None:
    """The same workflow must ask for the same resources whatever the executor.

    `k8s_job` honours `spec.cpu`/`spec.memory`; ACA ignored them and imposed its own, so a
    step that asked for 2 vCPU got 1 depending on the backend, without a word.
    """
    sent_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "PUT":
            sent_body.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    aca = executor(handler, cpu=0.5, memory="1Gi")
    job = spec()
    job.cpu = "2"
    job.memory = "4Gi"
    await aca.start(job)
    resources = sent_body["properties"]["template"]["containers"][0]["resources"]
    assert resources == {"cpu": 2.0, "memory": "4Gi"}

    # An unreadable value falls back to the executor's: a run must not fail for that.
    sent_body.clear()
    aca2 = executor(handler, cpu=0.5, memory="1Gi")
    bancal = spec("run-43")
    bancal.cpu = "beaucoup"
    await aca2.start(bancal)
    assert sent_body["properties"]["template"]["containers"][0]["resources"]["cpu"] == 0.5
