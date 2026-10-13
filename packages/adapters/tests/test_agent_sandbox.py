"""The `agent_sandbox` executor (S35-02, ADR 0016), against the scriptable fake cluster.

What is proved without a cluster: a warm sandbox holds no token before a claim binds it; the
token reaches exactly the pod bound to its run, once; a sandbox serves one run and is never
handed out again; the queue holds runs without a claim and admits them in arrival order; the
policy's RuntimeClass is never silently dropped; what the executor writes fits the pinned CRDs.
What only a cluster proves is in `tests/cluster/test_agent_sandbox.py`.
"""

from __future__ import annotations

import base64
import importlib.util
import pathlib
from types import ModuleType
from typing import Any

import httpx
import pytest
from choregos_adapters.errors import ConfigurationError, UpstreamError
from choregos_adapters.executor.agent_sandbox import AgentSandboxExecutor
from choregos_adapters.fakes.agent_sandbox import FakeSandboxCluster
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, StageJobSpec

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _crds() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "agent_sandbox_crds", ROOT / "tests" / "fixtures" / "agent_sandbox" / "crds.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _spec(run_id: str, token: str = "token-1", **extra: Any) -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="demo",
        namespace="choregos",
        runner_image="harbor.example/choregos-runner:0.17.12",
        api_url="http://choregos-api:8000/api/v1/internal",
        run_token=token,
        env={"HTTP_PROXY": "http://proxy:3128"},
        labels={"choregos/work-item": "DEMO-1"},
        **extra,
    )


def _executor(cluster: FakeSandboxCluster, **options: Any) -> AgentSandboxExecutor:
    return AgentSandboxExecutor(
        client=cluster,  # type: ignore[arg-type]
        http=httpx.AsyncClient(transport=cluster.transport),
        **options,
    )


def _ref(run_id: str) -> ExecRef:
    return ExecRef(kind=ExecutorKind.AGENT_SANDBOX, name=f"run-{run_id}", namespace="choregos", run_id=run_id)


def _tokens_seen(cluster: FakeSandboxCluster) -> dict[str, list[str]]:
    return {
        pod.name: [a["run_token"] for a in pod.assignments] for pod in cluster.all_pods() if pod.assignments
    }


async def test_a_warm_sandbox_holds_no_token_until_a_claim_binds_it() -> None:
    cluster = FakeSandboxCluster()
    warm = cluster.warm(2)
    executor = _executor(cluster)

    ref = await executor.start(_spec("r-1", token="secret-r1"))
    # Claimed but not yet bound by the controller: nothing has left the run's Secret.
    assert (await executor.status(ref)).state == "pending"
    assert _tokens_seen(cluster) == {}

    cluster.reconcile()
    sandbox = cluster.sandbox_of("r-1")
    assert sandbox is not None and sandbox.name in warm, "a warm sandbox is adopted"
    status = await executor.status(ref)
    assert status.state == "running"
    # Exactly one pod has the token: the one bound to THIS run's claim. The other warm one: nothing.
    assert _tokens_seen(cluster) == {sandbox.name: ["secret-r1"]}
    pushed = sandbox.pods[0].assignments[0]
    assert pushed["run_id"] == "r-1"
    assert pushed["api_url"] == "http://choregos-api:8000/api/v1/internal"
    assert pushed["env"]["HTTP_PROXY"] == "http://proxy:3128"


async def test_the_assignment_is_pushed_once() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    executor = _executor(cluster)
    ref = await executor.start(_spec("r-1"))
    cluster.reconcile()
    for _ in range(3):
        assert (await executor.status(ref)).state == "running"
    pod = cluster.pod_of("r-1")
    assert pod is not None and len(pod.assignments) == 1


async def test_a_sandbox_serves_one_run_and_is_never_handed_out_again() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    executor = _executor(cluster)
    first = await executor.start(_spec("r-1", token="t1"))
    cluster.reconcile()
    await executor.status(first)
    used = cluster.sandbox_of("r-1")
    assert used is not None

    cluster.finish("r-1", exit_code=0)
    assert (await executor.status(first)).state == "succeeded"
    await executor.cancel(first)  # what the controller's ttlSecondsAfterFinished does later
    assert used.name in cluster.deleted

    second = await executor.start(_spec("r-2", token="t2"))
    cluster.reconcile()
    await executor.status(second)
    fresh = cluster.sandbox_of("r-2")
    assert fresh is not None and fresh.name != used.name
    assert fresh.cold, "the pool was empty: a cold start from the template, never a used sandbox"
    for pod in cluster.all_pods():
        assert len({a["run_id"] for a in pod.assignments}) <= 1, f"{pod.name} served two runs"


async def test_a_sandbox_that_already_serves_another_run_fails_the_run() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    executor = _executor(cluster)
    ref = await executor.start(_spec("r-1"))
    cluster.reconcile()
    sandbox = cluster.sandbox_of("r-1")
    assert sandbox is not None and sandbox.pod is not None
    sandbox.pod.assignments.append({"run_id": "intruder", "run_token": "x"})
    with pytest.raises(UpstreamError, match="already serves another run"):
        await executor.status(ref)


async def test_a_run_with_a_policy_runtime_class_the_pool_lacks_is_refused() -> None:
    """`sandbox.runtime: gvisor` must not be bypassed by a pool made without it."""
    cluster = FakeSandboxCluster()
    with pytest.raises(ConfigurationError, match="RuntimeClass"):
        await _executor(cluster).start(_spec("r-1", runtime_class="gvisor"))
    assert cluster.claims == {} and cluster.secrets == {}
    await _executor(cluster, runtime_class="gvisor").start(_spec("r-2", runtime_class="gvisor"))
    assert "run-r-2" in cluster.claims


async def test_the_token_lives_in_a_secret_owned_by_the_claim() -> None:
    cluster = FakeSandboxCluster()
    executor = _executor(cluster)
    await executor.start(_spec("r-1", token="secret-r1"))
    secret = cluster.secrets["run-r-1"]
    assert base64.b64decode(secret["data"]["token"]).decode() == "secret-r1"
    (owner,) = secret["metadata"]["ownerReferences"]
    assert (owner["kind"], owner["name"]) == ("SandboxClaim", "run-r-1")
    claim = cluster.claims["run-r-1"]
    assert "secret-r1" not in str(claim), (
        "a token in the claim's spec would be readable by anyone who reads claims"
    )
    cluster.expire("r-1")
    assert "run-r-1" not in cluster.secrets, "the claim's end takes the token with it"


async def test_renew_replaces_the_token_before_the_push() -> None:
    cluster = FakeSandboxCluster()
    executor = _executor(cluster)
    ref = await executor.start(_spec("r-1", token="old"))
    await executor.renew(ref, "fresh")
    cluster.reconcile()
    await executor.status(ref)
    pod = cluster.pod_of("r-1")
    assert pod is not None and [a["run_token"] for a in pod.assignments] == ["fresh"]


async def test_start_is_idempotent() -> None:
    cluster = FakeSandboxCluster()
    executor = _executor(cluster)
    await executor.start(_spec("r-1"))
    await executor.start(_spec("r-1"))
    assert list(cluster.claims) == ["run-r-1"]
    assert [m for m, p in cluster.calls if p.endswith("/sandboxclaims")].count("POST") == 1


async def test_the_queue_holds_runs_without_a_claim_and_admits_in_arrival_order() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(3)
    executor = _executor(cluster, max_active=1)
    first = await executor.start(_spec("r-1"))
    second = await executor.start(_spec("r-2"))
    third = await executor.start(_spec("r-3"))
    assert list(cluster.claims) == ["run-r-1"], "beyond the cap: no claim, so no pod and no image pull"

    waiting = await executor.status(third)
    assert waiting.state == "pending" and "waiting for a slot" in waiting.message
    assert list(cluster.claims) == ["run-r-1"], "the third does not jump the second"

    cluster.reconcile()
    await executor.status(first)
    cluster.finish("r-1")
    await executor.status(third)
    assert sorted(cluster.claims) == ["run-r-1"], "still not the third's turn"
    admitted = await executor.status(second)
    assert admitted.state == "pending" and not admitted.message
    assert sorted(cluster.claims) == ["run-r-1", "run-r-2"]


async def test_logs_are_the_runner_pod_log() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    executor = _executor(cluster)
    ref = await executor.start(_spec("r-1"))
    cluster.reconcile()
    pod = cluster.pod_of("r-1")
    assert pod is not None
    pod.log.extend(["runner: started", "runner: result posted"])
    lines = [line async for line in executor.logs(ref)]
    assert lines[0].startswith("choregos-assignment-key: "), "the public key, printed at boot"
    assert lines[1:] == ["runner: started", "runner: result posted"]
    assert [line async for line in executor.logs(_ref("absent"))] == []


async def test_the_bytes_on_the_wire_never_contain_the_token_or_a_secret() -> None:
    """Sealed to the pod's key (ADR 0053): an eavesdropper on the pod network reads the run id."""
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    cluster.secrets["git-credentials"] = {
        "metadata": {"name": "git-credentials"},
        "data": {"GIT_TOKEN": base64.b64encode(b"ghp_secretvalue").decode()},
    }
    executor = _executor(cluster, env_from_secrets=["git-credentials"])
    ref = await executor.start(_spec("r-1", token="secret-r1"))
    cluster.reconcile()
    assert (await executor.status(ref)).state == "running"
    pod = cluster.pod_of("r-1")
    assert pod is not None and pod.wire, "something was pushed"
    for body in pod.wire:
        for clear in (b"secret-r1", b"ghp_secretvalue", b"proxy:3128", base64.b64encode(b"secret-r1")):
            assert clear not in body, f"{clear!r} crossed the pod network in clear"
    assert pod.assignments[0]["run_token"] == "secret-r1", "and the pod, only it, opened it"
    assert pod.private is None, "its key is dropped once used"


async def test_a_pod_that_never_printed_a_key_gets_nothing() -> None:
    cluster = FakeSandboxCluster()
    cluster.publish_keys = False
    cluster.warm(1)
    executor = _executor(cluster)
    ref = await executor.start(_spec("r-1", token="secret-r1"))
    cluster.reconcile()
    for _ in range(3):
        assert (await executor.status(ref)).state == "pending"
    pod = cluster.pod_of("r-1")
    assert pod is not None and pod.wire == [] and pod.assignments == []
    assert "choregos/assigned-at" not in str(cluster.claims["run-r-1"])


async def test_a_failed_pod_says_why() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    executor = _executor(cluster)
    ref = await executor.start(_spec("r-1"))
    cluster.reconcile()
    await executor.status(ref)
    cluster.finish("r-1", exit_code=3)
    status = await executor.status(ref)
    assert status.state == "failed" and status.exit_code == 3
    assert "runner exited with 3" in status.message


async def test_deployment_wide_secrets_reach_the_run_not_the_warm_pod() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    cluster.secrets["git-credentials"] = {
        "metadata": {"name": "git-credentials"},
        "data": {"GIT_TOKEN": base64.b64encode(b"ghp_x").decode()},
    }
    executor = _executor(cluster, env_from_secrets=["git-credentials"])
    ref = await executor.start(_spec("r-1"))
    cluster.reconcile()
    await executor.status(ref)
    pod = cluster.pod_of("r-1")
    assert pod is not None and pod.assignments[0]["env"]["GIT_TOKEN"] == "ghp_x"


async def test_cancel_deletes_claim_sandbox_and_secret() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    executor = _executor(cluster, max_active=1)
    running = await executor.start(_spec("r-1"))
    queued = await executor.start(_spec("r-2"))
    cluster.reconcile()
    await executor.cancel(running)
    await executor.cancel(queued)
    assert cluster.claims == {} and cluster.secrets == {} and cluster.sandboxes == {}


async def test_what_the_executor_writes_fits_the_pinned_crds() -> None:
    cluster = FakeSandboxCluster()
    await _executor(cluster).start(_spec("r-1"))
    claim = cluster.claims["run-r-1"]
    written = {k: v for k, v in claim.items() if k != "status"}
    written["metadata"] = {
        k: v for k, v in claim["metadata"].items() if k not in {"uid", "creationTimestamp"}
    }
    assert _crds().problems(written) == []


def test_the_executor_speaks_the_pinned_release() -> None:
    from choregos_adapters.executor import agent_sandbox

    pinned = _crds().pinned()
    assert pinned["release"] == agent_sandbox.AGENT_SANDBOX_RELEASE
    kinds = pinned["kinds"]
    assert f"/apis/{kinds['Sandbox']['group']}/{kinds['Sandbox']['version']}" == agent_sandbox.SANDBOX_API
    claim = kinds["SandboxClaim"]
    assert f"/apis/{claim['group']}/{claim['version']}" == agent_sandbox.EXTENSIONS_API


#: The REST verb → the RBAC verb it needs. A `GET` on a collection needs `list`.
_RBAC = {"POST": "create", "PUT": "update", "PATCH": "patch", "DELETE": "delete"}


def _granted() -> dict[str, set[str]]:
    import yaml

    role = ROOT / "charts/choregos/charts/orchestrator/templates/rbac-jobs.yaml"
    text = role.read_text(encoding="utf-8")
    start = text.index("rules:")
    lines = text[start : text.index("---", start)].splitlines()
    block = "\n".join(line for line in lines if not line.strip().startswith("{{"))
    granted: dict[str, set[str]] = {}
    for rule in yaml.safe_load(block)["rules"]:
        for resource in rule["resources"]:
            granted.setdefault(resource, set()).update(rule["verbs"])
    return granted


async def test_the_role_grants_every_verb_the_executor_uses() -> None:
    """Twice a missing verb killed tickets with a 403 (`test_rbac_du_job`). Here the calls are
    not read in the source: the executor is driven through whole runs, queue, renew, logs and
    cancel included, and every call it made is checked against the chart's Role."""
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    executor = _executor(cluster, max_active=1)
    first = await executor.start(_spec("r-1"))
    second = await executor.start(_spec("r-2"))
    await executor.renew(second, "fresh")
    cluster.reconcile()
    await executor.status(first)
    _ = [line async for line in executor.logs(first)]
    cluster.finish("r-1", exit_code=2)
    await executor.status(first)
    await executor.status(second)
    await executor.cancel(first)
    await executor.cancel(second)

    used: set[tuple[str, str]] = set()
    for method, path in cluster.calls:
        parts = path.strip("/").split("/")
        at = parts.index("namespaces") + 2
        resource = "pods/log" if parts[-1] == "log" else parts[at]
        verb = _RBAC.get(method) or ("get" if len(parts) > at + 1 else "list")
        used.add((resource, verb))
    assert {r for r, _ in used} >= {"sandboxclaims", "sandboxes", "secrets", "pods", "pods/log"}, used
    granted = _granted()
    missing = sorted((r, v) for r, v in used if v not in granted.get(r, set()))
    assert missing == [], f"the executor does {missing}; the Role does not grant it: a 403 kills the ticket"
