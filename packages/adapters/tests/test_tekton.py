"""Tekton, executor and CI: what the Kubernetes API receives, without a cluster."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from choregos_adapters.ci.tekton import TektonCi
from choregos_adapters.errors import UpstreamError
from choregos_adapters.executor.tekton import CORE_API, TEKTON_API, KubernetesClient, TektonExecutor
from choregos_adapters.fakes.kubernetes import FakeKubernetesApi
from choregos_contracts import InboundEventType
from choregos_core.domain import ExecRef, StageJobSpec

from ._transport import Wire

K8S = "https://k8s.test"
NS = "proj-billing-runners"


def k8s(wire: Wire) -> KubernetesClient:
    return KubernetesClient(K8S, token="sa-token", client=wire.client())


def spec(**kw: object) -> StageJobSpec:
    base: dict[str, object] = {
        "run_id": "wi-1-t-implement-1",
        "project_slug": "billing",
        "namespace": NS,
        "runner_image": "registry.example/choregos-runner:0.3.0",
        "api_url": "http://choregos-api:8000",
        "run_token": "jeton-de-run",
        "timeout_minutes": 40,
        "labels": {"choregos/work-item": "billing-12"},
    }
    base.update(kw)
    return StageJobSpec(**base)  # type: ignore[arg-type]


async def test_the_kubernetes_client_translates_404_into_absence_and_the_rest_into_errors() -> None:
    wire = Wire({("GET", "/x"): (403, "forbidden: no RBAC")})
    client = k8s(wire)
    assert await client.request("GET", "/absent") is None
    with pytest.raises(UpstreamError, match="403") as exc:
        await client.request("GET", "/x")
    assert exc.value.service == "kubernetes"
    assert wire.requests[0].headers["Authorization"] == "Bearer sa-token"


async def test_a_patch_announces_its_content_type_without_losing_the_token() -> None:
    wire = Wire({("PATCH", "/x"): (200, {})})
    await k8s(wire).request("PATCH", "/x", json={}, headers={"Content-Type": "application/merge-patch+json"})
    headers_seen = wire.requests[0].headers
    assert (
        headers_seen["Content-Type"] == "application/merge-patch+json"
        and headers_seen["Authorization"] == "Bearer sa-token"
    )


async def test_start_creates_the_runs_secret_then_the_pipelinerun() -> None:
    wire = Wire(
        {
            ("POST", f"{CORE_API}/namespaces/{NS}/secrets"): (201, {}),
            ("POST", f"{TEKTON_API}/namespaces/{NS}/pipelineruns"): (201, {}),
        }
    )
    ref = await TektonExecutor(k8s(wire), pipeline="choregos-agent").start(spec(runtime_class="gvisor"))
    assert ref.name == "run-wi-1-t-implement-1" and ref.namespace == NS
    assert wire.sent("POST") == [
        ("POST", f"{CORE_API}/namespaces/{NS}/secrets"),
        ("POST", f"{TEKTON_API}/namespaces/{NS}/pipelineruns"),
    ]
    secret = wire.body(-2)
    assert base64.b64decode(secret["data"]["token"]).decode() == "jeton-de-run"
    assert secret["metadata"]["labels"]["choregos/run-id"] == "wi-1-t-implement-1"
    pr = wire.body(-1)
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["repo-url"] == "" and params["revision"] == "main", (
        "without a repository: the clone is skipped"
    )
    assert params["runner-image"].endswith(":0.3.0")
    assert pr["spec"]["timeouts"] == {"pipeline": "40m"}
    template = pr["spec"]["taskRunTemplate"]["podTemplate"]
    assert template["runtimeClassName"] == "gvisor" and template["securityContext"]["runAsNonRoot"] is True
    assert pr["metadata"]["labels"]["choregos/work-item"] == "billing-12"


async def test_start_is_idempotent_by_run_name() -> None:
    wire = Wire(
        {
            ("GET", f"{TEKTON_API}/namespaces/{NS}/pipelineruns/run-wi-1-t-implement-1"): (
                200,
                {"metadata": {}},
            )
        }
    )
    ref = await TektonExecutor(k8s(wire)).start(spec())
    assert ref.run_id == "wi-1-t-implement-1" and wire.sent("POST") == []


async def test_the_state_reads_the_condition_and_the_results() -> None:
    path = f"{TEKTON_API}/namespaces/{NS}/pipelineruns/run-1"
    ok = {
        "status": {
            "conditions": [{"status": "True"}],
            "results": [{"name": "result-url", "value": "http://r"}],
            "startTime": "2026-09-25T07:00:00Z",
            "completionTime": "2026-09-25T07:10:00Z",
        }
    }
    cancelled = {"status": {"conditions": [{"status": "False", "reason": "Cancelled", "message": "stop"}]}}
    for payload, expected in ((ok, "succeeded"), (cancelled, "cancelled"), ({"status": {}}, "running")):
        wire = Wire({("GET", path): (200, payload)})
        state = await TektonExecutor(k8s(wire)).status(ExecRef(kind="tekton", name="run-1", namespace=NS))
        assert state.state == expected, payload
    assert (
        await TektonExecutor(k8s(Wire({("GET", path): (200, ok)}))).status(
            ExecRef(kind="tekton", name="run-1", namespace=NS)
        )
    ).result_url == "http://r"
    absent = await TektonExecutor(k8s(Wire())).status(ExecRef(kind="tekton", name="run-1", namespace=NS))
    assert absent.state == "unknown" and "introuvable" in absent.message


async def test_cancel_stops_the_pipelinerun_then_deletes_the_secret() -> None:
    wire = Wire(
        {
            ("PATCH", f"{TEKTON_API}/namespaces/{NS}/pipelineruns/run-1"): (200, {}),
            ("DELETE", f"{CORE_API}/namespaces/{NS}/secrets/run-1"): (200, {}),
        }
    )
    await TektonExecutor(k8s(wire)).cancel(ExecRef(kind="tekton", name="run-1", namespace=NS))
    assert wire.sent() == [
        ("PATCH", f"{TEKTON_API}/namespaces/{NS}/pipelineruns/run-1"),
        ("DELETE", f"{CORE_API}/namespaces/{NS}/secrets/run-1"),
    ]
    assert wire.body(0) == {"spec": {"status": "Cancelled"}}


async def test_the_logs_come_from_the_step_run_container_of_each_pod() -> None:
    wire = Wire(
        {
            ("GET", f"{CORE_API}/namespaces/{NS}/pods"): (200, {"items": [{"metadata": {"name": "p1"}}]}),
            ("GET", f"{CORE_API}/namespaces/{NS}/pods/p1/log"): (200, "line 1\nline 2"),
        }
    )
    lines = [
        line
        async for line in TektonExecutor(k8s(wire)).logs(ExecRef(kind="tekton", name="run-1", namespace=NS))
    ]
    assert lines == ["line 1", "line 2"]
    assert wire.requests[0].url.params["labelSelector"] == "tekton.dev/pipelineRun=run-1"
    assert wire.requests[1].url.params["container"] == "step-run"


async def test_ci_aggregates_the_pipelineruns_of_a_sha() -> None:
    runs = {
        "items": [
            {"metadata": {"name": "ci-1"}, "status": {"conditions": [{"status": "True"}]}},
            {"metadata": {"name": "ci-2"}, "status": {"conditions": [{"status": "Unknown"}]}},
        ]
    }
    wire = Wire({("GET", f"{TEKTON_API}/namespaces/ci/pipelineruns"): (200, runs)})
    ci_status = await TektonCi(k8s(wire), namespace="ci").status_for("varga/billing", "abc")
    assert ci_status.state == "running" and [c.status for c in ci_status.runs] == ["completed", "in_progress"]
    assert wire.requests[0].url.params["labelSelector"] == "choregos/sha=abc"
    vide = await TektonCi(
        k8s(Wire({("GET", f"{TEKTON_API}/namespaces/ci/pipelineruns"): (200, {"items": []})})), namespace="ci"
    ).status_for("r", "abc")
    assert vide.state == "unknown"
    echec = {"items": [{"metadata": {"name": "ci-1"}, "status": {"conditions": [{"status": "False"}]}}]}
    assert (
        await TektonCi(
            k8s(Wire({("GET", f"{TEKTON_API}/namespaces/ci/pipelineruns"): (200, echec)})), namespace="ci"
        ).status_for("r", "abc")
    ).state == "failure"


async def test_triggering_a_pipeline_passes_repository_and_revision() -> None:
    wire = Wire(
        {
            ("POST", f"{TEKTON_API}/namespaces/ci/pipelineruns"): (
                201,
                {"metadata": {"name": "choregos-ci-x7"}},
            )
        }
    )
    name = await TektonCi(k8s(wire), namespace="ci").trigger("varga/billing", "abc", "")
    assert name == "choregos-ci-x7"
    body = wire.body(-1)
    assert body["metadata"]["generateName"] == "choregos-ci-"
    assert {p["name"]: p["value"] for p in body["spec"]["params"]} == {
        "repo-url": "https://github.com/varga/billing.git",
        "revision": "abc",
    }


def test_a_tekton_cloudevent_becomes_a_ci_event() -> None:
    body = {
        "pipelineRun": {
            "metadata": {
                "name": "run-1",
                "labels": {
                    "choregos/project": "billing",
                    "choregos/work-item": "billing-12",
                    "choregos/sha": "abc",
                    "choregos/run-id": "r-1",
                },
            }
        }
    }
    import json

    (event,) = TektonCi(k8s(Wire())).parse_event(
        {"ce-type": "dev.tekton.event.pipelinerun.failed.v1", "ce-id": "ev-1"}, json.dumps(body).encode()
    )
    assert event.type == InboundEventType.CI_FAILED and event.delivery_id == "ev-1"
    assert event.project_slug == "billing" and event.work_item_key == "billing-12"
    assert event.payload == {"pipeline_run": "run-1", "sha": "abc", "run_id": "r-1"}


# ──────── the run's Secret never outlives its PipelineRun (S22-39, #501) ────────


def _owned_by(secret: dict[str, Any], owner: dict[str, Any]) -> bool:
    refs = secret["metadata"].get("ownerReferences") or []
    return any(r["uid"] == owner["metadata"]["uid"] and r["kind"] == "PipelineRun" for r in refs)


async def test_the_secret_of_a_run_goes_with_its_pipeline_run() -> None:
    """#501: the Secret (run token and gateway key) had no owner, so it survived every run,
    including normal ends. It now belongs to its PipelineRun: when Tekton's pruner (or anyone)
    deletes the PipelineRun, the garbage collector takes the Secret."""
    api = FakeKubernetesApi()
    await TektonExecutor(client=api).start(spec(gateway_key="sk-run"))  # type: ignore[arg-type]
    name = "run-wi-1-t-implement-1"
    assert _owned_by(api.secrets[name], api.pipelineruns[name])
    api.collect("pipelineruns", name)
    assert api.secrets == {}, "the token outlived its PipelineRun"


async def test_a_start_replayed_after_a_refused_handover_still_gives_the_secret_away() -> None:
    """The handover failed after the PipelineRun was made; the activity retries, finds the
    PipelineRun, and hands the Secret over then (the same 403 as #499 on `k8s_job`)."""
    api = FakeKubernetesApi()
    executor = TektonExecutor(client=api)  # type: ignore[arg-type]
    api.refuse[("PATCH", "/secrets/run-wi-1-t-implement-1")] = 403
    with pytest.raises(UpstreamError, match="403"):
        await executor.start(spec())
    assert "run-wi-1-t-implement-1" in api.secrets, "the PipelineRun exists and needs its token"

    await executor.start(spec())  # the activity's retry
    api.collect("pipelineruns", "run-wi-1-t-implement-1")
    assert api.secrets == {}


async def test_a_pipeline_run_that_cannot_be_created_leaves_no_secret_behind() -> None:
    api = FakeKubernetesApi()
    api.refuse[("POST", "/pipelineruns")] = 403
    with pytest.raises(UpstreamError, match="403"):
        await TektonExecutor(client=api).start(spec())  # type: ignore[arg-type]
    assert api.pipelineruns == {} and api.secrets == {}


async def test_a_pipeline_run_created_despite_a_lost_answer_keeps_its_secret() -> None:
    """The create reached the cluster, its answer was lost: the Secret is only deleted when the
    PipelineRun really does not exist."""

    class _LostAnswer(FakeKubernetesApi):
        async def request(self, method: str, path: str, **kwargs: Any) -> Any:
            answer = await super().request(method, path, **kwargs)
            if method == "POST" and path.endswith("/pipelineruns"):
                raise TimeoutError("answer lost")
            return answer

    api = _LostAnswer()
    with pytest.raises(TimeoutError):
        await TektonExecutor(client=api).start(spec())  # type: ignore[arg-type]
    assert "run-wi-1-t-implement-1" in api.secrets
