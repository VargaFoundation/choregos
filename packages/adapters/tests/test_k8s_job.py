"""The `k8s_job` executor: what the Job carries, checked without a cluster."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from choregos_adapters.executor.k8s_job import RUNNER_POD_LABELS, KubernetesJobExecutor
from choregos_adapters.fakes.kubernetes import FakeKubernetesApi
from choregos_adapters.registry import default_executor_kind
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, StageJobSpec


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs.get("json")))
        return None  # nothing exists yet: the Job and its secret are created


def _spec() -> StageJobSpec:
    return StageJobSpec(
        run_id="r-1",
        project_slug="demo",
        namespace="choregos",
        runner_image="harbor.example/ghcr-proxy/example/choregos-runner:0.1.0",
        api_url="http://choregos-api:8000",
        run_token="jeton",
    )


async def test_the_runner_pod_carries_a_stable_label_for_the_network_policies() -> None:
    """The network policies select runners by family: without a stable label,
    a runner pod reaches nothing out, or the whole namespace must be opened."""
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    labels = job["spec"]["template"]["metadata"]["labels"]
    assert labels["choregos/run-id"] == "r-1"
    assert RUNNER_POD_LABELS.items() <= labels.items()


async def test_the_runner_pod_mounts_no_kubernetes_token() -> None:
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["serviceAccountName"] == "choregos-runner"


async def test_the_runner_pods_limits_follow_the_configuration() -> None:
    """Under a LimitRange of 4 Gi per container, a 6 Gi ceiling makes the pod refused."""
    client = _RecordingClient()
    executor = KubernetesJobExecutor(client=client, cpu_limit="1500m", memory_limit="4Gi")  # type: ignore[arg-type]
    await executor.start(_spec())
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    limits = job["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
    assert limits == {"cpu": "1500m", "memory": "4Gi"}


def test_a_project_without_a_runtime_takes_the_deployments_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    assert default_executor_kind() == "k8s_job"
    monkeypatch.delenv("CHOREGOS_EXECUTOR_KIND")
    assert default_executor_kind() == "agent_sandbox", "ADR 0072: Tekton is no longer assumed"


def test_the_allowed_executors_default_to_the_deployment_s(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_adapters.registry import allowed_executor_kinds, executors_declared

    monkeypatch.delenv("CHOREGOS_EXECUTOR_KIND", raising=False)
    monkeypatch.delenv("CHOREGOS_EXECUTORS_ALLOWED", raising=False)
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    assert allowed_executor_kinds() == ["agent_sandbox"]
    assert executors_declared() is False
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    monkeypatch.setenv("CHOREGOS_EXECUTORS_ALLOWED", "agent_sandbox, k8s_job,")
    assert allowed_executor_kinds() == ["k8s_job", "agent_sandbox"], "the default comes first, no duplicate"
    assert executors_declared() is True
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    assert allowed_executor_kinds()[-1] == "fake"


def test_the_memory_takes_the_deployments_url_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CHOREGOS_MEMORY_URL` was set by the chart and ignored: every project aimed at
    `ecphoria.choregos-memory`, a service that exists only in one installation."""
    from choregos_adapters import build

    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)  # CI runs on fakes
    monkeypatch.setenv("CHOREGOS_MEMORY_URL", "http://ecphoria:8432")
    monkeypatch.setenv("CHOREGOS_MEMORY_TOKEN", "cle")
    memory = build("memory", "ecphoria", {})
    assert memory.base_url == "http://ecphoria:8432"
    assert memory.token == "cle"


async def test_agent_secrets_are_mounted_by_reference() -> None:
    """By `envFrom`, never copied: a value in the Job's spec would be readable by
    whoever can read a pod, and would stay in the API's history."""
    client = _RecordingClient()
    executor = KubernetesJobExecutor(client=client, env_from_secrets=["agent-creds", "git-creds"])  # type: ignore[arg-type]
    await executor.start(_spec())
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["envFrom"] == [
        {"secretRef": {"name": "agent-creds"}},
        {"secretRef": {"name": "git-creds"}},
    ]
    assert not any("agent-creds" in str(entry.get("value", "")) for entry in container["env"])


async def test_without_a_declared_secret_the_pod_has_no_envfrom() -> None:
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    assert "envFrom" not in job["spec"]["template"]["spec"]["containers"][0]


async def test_the_direct_gateway_returns_the_given_key_and_measures_nothing() -> None:
    """Without a gateway, there is neither ceiling nor cost: `spend()` returns zero, which means
    "not measured": the cost table will show 0, which is the accepted counterpart."""
    from choregos_adapters.gateway.direct import DirectGateway

    gateway = DirectGateway(key="jeton-fourni", models=["anthropic/claude-sonnet-5"])
    minted = await gateway.mint_key({"run": "r-1"}, budget_usd=1.0, ttl_s=60, models=["m"])
    assert minted.key == "jeton-fourni"
    assert (await gateway.spend(minted.key_id)).cost_usd == 0.0
    assert [m.model_name for m in await gateway.list_models()] == ["anthropic/claude-sonnet-5"]


def test_without_a_run_key_the_backend_leaves_the_authentication_variables_alone() -> None:
    """The agent then brings its own credentials (subscription): writing an empty key
    would overwrite them, and the failure would look like a refusal by the provider."""
    from choregos_contracts import ModelRef
    from choregos_runner.backends.base import Backend

    model = ModelRef(litellm_model="anthropic/claude-sonnet-5", base_url="https://api.anthropic.com")
    assert Backend.anthropic_env(model, None) == {"ANTHROPIC_MODEL": "anthropic/claude-sonnet-5"}
    assert Backend.openai_env(model, None) == {"OPENAI_MODEL": "anthropic/claude-sonnet-5"}
    with_key = Backend.anthropic_env(model, "sk-run")
    assert with_key["ANTHROPIC_AUTH_TOKEN"] == "sk-run"


async def test_the_direct_gateway_gives_a_key_identifier_per_run() -> None:
    """`key_id` carries a unique index in the database: a constant identifier would make the
    second run fail on a constraint violation."""
    from choregos_adapters.gateway.direct import DirectGateway

    gateway = DirectGateway(key="jeton")
    first_key = await gateway.mint_key({"run_id": "r-1"}, budget_usd=1, ttl_s=60, models=[])
    second_key = await gateway.mint_key({"run_id": "r-2"}, budget_usd=1, ttl_s=60, models=[])
    sans = await gateway.mint_key({}, budget_usd=1, ttl_s=60, models=[])
    assert first_key.key_id != second_key.key_id
    assert sans.key_id.startswith("direct:")


async def test_a_replayed_run_rewrites_its_token() -> None:
    """The secret's name is deterministic: replaying a run lands on it. Leaving it as it
    was would make the agent run with a stale token, and the API would answer "Signature
    verification failed", a message that blames the signature, not the secret."""

    class _WithSecret(_RecordingClient):
        async def request(self, method: str, path: str, **kwargs: Any) -> Any:
            await super().request(method, path, **kwargs)
            return {"kind": "Secret"} if method == "GET" and "/secrets/" in path else None

    client = _WithSecret()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    ecritures = [(m, p) for m, p, _ in client.calls if m in {"PUT", "POST"} and "/secrets" in p]
    assert ecritures and ecritures[0][0] == "PUT"


class _ClusterClient:
    """A pocket cluster: it keeps the Jobs created and answers lists and patches."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.horloge = 0
        self.calls: list[tuple[str, str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        body = kwargs.get("json")
        self.calls.append((method, path, body))
        if method == "POST" and path.endswith("/jobs"):
            self.horloge += 1
            body["metadata"]["creationTimestamp"] = f"2026-09-24T10:00:{self.horloge:02d}Z"
            body.setdefault("status", {})
            self.jobs[body["metadata"]["name"]] = body
            return body
        if method == "GET" and path.endswith("/jobs"):
            return {"items": list(self.jobs.values())}
        if path.rsplit("/", 1)[-1] in self.jobs and "/jobs/" in path:
            name = path.rsplit("/", 1)[-1]
            if method == "GET":
                return self.jobs[name]
            if method == "PATCH":
                assert kwargs["headers"]["Content-Type"] == "application/merge-patch+json"
                self.jobs[name]["spec"].update(body["spec"])
                return self.jobs[name]
        return None

    def actifs(self) -> list[str]:
        return [n for n, j in self.jobs.items() if not j["spec"].get("suspend")]


def _spec_for(run_id: str) -> StageJobSpec:
    spec = _spec()
    return spec.model_copy(update={"run_id": run_id})


async def test_beyond_the_ceiling_the_job_waits_without_a_pod() -> None:
    """Ten tickets that start together make ten pods pulling the same image at the same
    time, and a namespace quota that refuses the eleventh with no explanation. A Job
    `suspend: true` has NO pod: it waits, visible, consuming nothing."""
    client = _ClusterClient()
    executor = KubernetesJobExecutor(client=client, max_active=2)  # type: ignore[arg-type]
    for i in range(4):
        await executor.start(_spec_for(f"r-{i}"))

    assert client.actifs() == ["run-r-0", "run-r-1"], client.actifs()
    assert all(client.jobs[f"run-r-{i}"]["spec"]["suspend"] for i in (2, 3))

    state = await executor.status(
        ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-2", namespace="choregos", run_id="r-2")
    )
    assert state.state == "pending"
    assert "waiting for a slot" in state.message


async def test_the_queue_advances_in_arrival_order() -> None:
    """Without order, the last one laid down would go ahead at every poll and an unlucky run
    would wait indefinitely."""
    client = _ClusterClient()
    executor = KubernetesJobExecutor(client=client, max_active=2)  # type: ignore[arg-type]
    for i in range(4):
        await executor.start(_spec_for(f"r-{i}"))

    client.jobs["run-r-0"]["status"] = {"succeeded": 1}  # a place frees up

    # The last to arrive asks for its state: it must NOT go ahead of the third.
    last = ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-3", namespace="choregos", run_id="r-3")
    assert "waiting for a slot" in (await executor.status(last)).message
    assert client.jobs["run-r-3"]["spec"]["suspend"]

    troisieme = ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-2", namespace="choregos", run_id="r-2")
    await executor.status(troisieme)
    assert not client.jobs["run-r-2"]["spec"].get("suspend"), "the oldest waiting one goes through"


async def test_without_a_ceiling_nothing_changes() -> None:
    """A deployment that asks for nothing does not change regime overnight."""
    client = _ClusterClient()
    executor = KubernetesJobExecutor(client=client)  # type: ignore[arg-type]
    for i in range(5):
        await executor.start(_spec_for(f"r-{i}"))
    assert len(client.actifs()) == 5


async def test_a_refused_admission_does_not_kill_the_run() -> None:
    """The `patch` right was missing from the Role: the API answered 403, the activity failed,
    and the ticket's workflow died. Three tickets lost on the bench of 2026-09-24, one of
    which expired without having run for a second. A failed admission must leave the run
    queued, not kill it: the next poll will try again."""

    class _WithoutPatchRight(_ClusterClient):
        async def request(self, method: str, path: str, **kwargs: Any) -> Any:
            if method == "PATCH":
                raise RuntimeError('[kubernetes] PATCH … → 403 : cannot patch resource "jobs"')
            return await super().request(method, path, **kwargs)

    client = _WithoutPatchRight()
    executor = KubernetesJobExecutor(client=client, max_active=1)  # type: ignore[arg-type]
    for i in range(2):
        await executor.start(_spec_for(f"r-{i}"))
    client.jobs["run-r-0"]["status"] = {"succeeded": 1}  # a place frees up

    state = await executor.status(
        ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-1", namespace="choregos", run_id="r-1")
    )
    assert state.state == "pending", "the run stays queued"
    assert "waiting for a slot" in state.message
    assert client.jobs["run-r-1"]["spec"]["suspend"], "still suspended, but alive"


async def test_a_queued_runs_token_can_be_replaced() -> None:
    """A token is minted at preparation and lives `max_minutes + 15`. With a queue, this
    countdown runs DURING the wait: a run admitted later started with a stale
    token and received "Signature has expired" on its first call, as seen on the
    bench of 2026-09-24. The secret has the Job's name: replacing it is enough."""
    client = _ClusterClient()
    executor = KubernetesJobExecutor(client=client, max_active=1)  # type: ignore[arg-type]
    await executor.start(_spec_for("r-0"))

    ref = ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-0", namespace="choregos", run_id="r-0")
    await executor.renew(ref, "jeton-tout-neuf")

    secret = next(
        body for method, path, body in client.calls if method == "PATCH" and path.endswith("/secrets/run-r-0")
    )
    assert base64.b64decode(secret["data"]["token"]).decode() == "jeton-tout-neuf"
    assert "renew" in KubernetesJobExecutor.capabilities


async def test_the_runner_pod_passes_the_strict_kyverno_rule() -> None:
    """`infra/policies/pod-security.yaml` requires the seccomp profile at the POD level, not
    only the container's: it is what the Job must carry to be admitted."""
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    pod = job["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    conteneur = pod["containers"][0]["securityContext"]
    assert conteneur["allowPrivilegeEscalation"] is False and conteneur["capabilities"] == {"drop": ["ALL"]}


class _ClientQuiNomme(_RecordingClient):
    """An API server returns the created object, with its `uid`: it is what carries the link."""

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        await super().request(method, path, **kwargs)
        if method == "POST" and path.endswith("/jobs"):
            return {**kwargs["json"], "metadata": {**kwargs["json"]["metadata"], "uid": "uid-du-job"}}
        return None


async def test_the_runs_secret_belongs_to_its_job() -> None:
    """A finished run's token must not outlive the Job: sixty-four orphan secrets
    in the bench namespace, one per run, as long as nothing attached them."""
    client = _ClientQuiNomme()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    patch = next(
        body for method, path, body in client.calls if method == "PATCH" and path.endswith("/secrets/run-r-1")
    )
    assert patch["metadata"]["ownerReferences"] == [
        {"apiVersion": "batch/v1", "kind": "Job", "name": "run-r-1", "uid": "uid-du-job"}
    ]
    order = [method for method, path, _ in client.calls if "/jobs" in path or "/secrets" in path]
    assert order.index("POST") < len(order) - 1, "the Job is created before the secret is attached to it"


async def test_without_a_uid_the_secret_stays_as_before() -> None:
    """A client that returns nothing (the tests, a stingy proxy) does not break the start."""
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    assert not [1 for method, path, _ in client.calls if method == "PATCH" and "/secrets/" in path]


# ──────── who serves the tool catalogue, and why not this Job (2026-09-27) ────────


async def test_the_agent_pod_has_only_one_container() -> None:
    """No tools sidecar here, and it is a verified CHOICE, not an oversight.

    One was added on 2026-09-27 on the belief that nothing served the catalogue under this
    executor. That was wrong: `runner/local_tools.py` starts this server itself when
    the announced address is local and nothing answers; its header describes exactly the defect
    that was thought to exist. The sidecar cost a container, an image pull and quota per agent pod
    to redo what worked, and it won the race for port 7777, so the runner's own server
    held back.

    This test exists so that nobody adds one without having read `local_tools.py`.
    """
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    pod = job["spec"]["template"]["spec"]
    assert "initContainers" not in pod, (
        "the catalogue is served by the runner itself (`local_tools.py`): a sidecar here "
        "redoes what works, and it takes the port before it"
    )
    assert [c["name"] for c in pod["containers"]] == ["runner"]


class _ClusterScripte:
    """A failed Job, and its run's pods as the test describes them."""

    def __init__(self, pods: list[dict[str, Any]]) -> None:
        self.pods = pods

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if path.endswith("/jobs/run-r-1"):
            conditions = [{"type": "Failed", "message": "Job has reached the specified backoff limit"}]
            return {"spec": {}, "status": {"failed": 1, "conditions": conditions}}
        if path.endswith("/pods"):
            return {"items": self.pods}
        return None


def _pod(cree: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": {"creationTimestamp": cree},
        "status": {"containerStatuses": [{"name": "runner", "state": state}]},
    }


async def test_a_dead_run_says_the_runners_exit_code_and_what_it_means() -> None:
    """On 06/10, a run of the HR scenario only said "backoff limit": the pod knew why."""
    # The API's list has no guaranteed order: the most recent one comes first here.
    client = _ClusterScripte(
        [
            _pod("2026-10-06T03:49:00Z", {"terminated": {"exitCode": 30, "reason": "Error"}}),
            _pod("2026-10-06T03:48:00Z", {"terminated": {"exitCode": 20, "reason": "Error"}}),
        ]
    )
    result_status = await KubernetesJobExecutor(client=client).status(  # type: ignore[arg-type]
        ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-1", namespace="choregos", run_id="r-1")
    )
    assert result_status.state == "failed"
    assert result_status.exit_code == 30, "le last pod fait foi"
    assert "runner exited with 30 (Error: agent backend unreachable)" in result_status.message
    assert "backoff limit" in result_status.message


async def test_a_runner_that_never_started_says_why() -> None:
    client = _ClusterScripte(
        [_pod("2026-10-06T03:48:00Z", {"waiting": {"reason": "ImagePullBackOff", "message": "pull refused"}})]
    )
    result_status = await KubernetesJobExecutor(client=client).status(  # type: ignore[arg-type]
        ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-1", namespace="choregos", run_id="r-1")
    )
    assert result_status.exit_code is None
    assert "runner never started: ImagePullBackOff - pull refused" in result_status.message


async def test_without_a_pod_the_run_says_so() -> None:
    result_status = await KubernetesJobExecutor(client=_ClusterScripte([])).status(  # type: ignore[arg-type]
        ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-1", namespace="choregos", run_id="r-1")
    )
    assert "no pod for the run" in result_status.message


async def test_an_oom_killed_runner_gives_its_termination_reason() -> None:
    """S36-02: the archive keeps the runtime's own word for how the pod ended."""
    client = _ClusterScripte(
        [_pod("2026-10-12T10:00:00Z", {"terminated": {"exitCode": 137, "reason": "OOMKilled"}})]
    )
    status = await KubernetesJobExecutor(client=client).status(  # type: ignore[arg-type]
        ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-1", namespace="choregos", run_id="r-1")
    )
    assert (status.exit_code, status.reason) == (137, "OOMKilled")


async def test_an_image_that_does_not_pull_gives_its_reason() -> None:
    client = _ClusterScripte([_pod("2026-10-12T10:00:00Z", {"waiting": {"reason": "ErrImagePull"}})])
    status = await KubernetesJobExecutor(client=client).status(  # type: ignore[arg-type]
        ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-1", namespace="choregos", run_id="r-1")
    )
    assert (status.exit_code, status.reason) == (None, "ErrImagePull")


# ──────── the run's token never outlives its run (S22-34, dev tenant 2026-10-14) ────────


_ApiServer = FakeKubernetesApi


def _ref_of(run_id: str) -> ExecRef:
    return ExecRef(kind=ExecutorKind.K8S_JOB, name=f"run-{run_id}", namespace="choregos", run_id=run_id)


async def test_the_token_of_a_run_that_ends_normally_goes_with_its_job() -> None:
    api = _ApiServer()
    await KubernetesJobExecutor(client=api).start(_spec_for("r-0"))  # type: ignore[arg-type]
    api.jobs["run-r-0"]["status"] = {"succeeded": 1}
    api.ttl_collects("run-r-0")
    assert api.secrets == {}


async def test_the_token_of_a_queued_run_survives_neither_its_renewal_nor_its_job() -> None:
    """Three of the five orphan Secrets on the dev tenant came from runs that waited for a slot
    on 2026-10-08: the renewal rewrote the Secret with a PUT that dropped its owner reference."""
    api = _ApiServer()
    executor = KubernetesJobExecutor(client=api, max_active=1)  # type: ignore[arg-type]
    await executor.start(_spec_for("r-0"))
    await executor.start(_spec_for("r-1"))  # queued: suspended Job
    await executor.renew(_ref_of("r-1"), "fresh-token")

    assert base64.b64decode(api.secrets["run-r-1"]["data"]["token"]).decode() == "fresh-token"
    assert api.secrets["run-r-1"]["metadata"]["labels"] == {"choregos/run-id": "r-1"}
    api.ttl_collects("run-r-1")
    assert "run-r-1" not in api.secrets, "the renewed token outlived its Job"


async def test_a_start_replayed_after_a_refused_handover_still_gives_the_token_to_the_job() -> None:
    """The two 2026-09-27 orphans: `PATCH .../secrets/run-... -> 403` after the Job was made;
    the activity retried, found the Job, and returned without handing the Secret over."""
    api = _ApiServer()
    executor = KubernetesJobExecutor(client=api)  # type: ignore[arg-type]
    api.refuse[("PATCH", "/secrets/run-r-0")] = 403
    with pytest.raises(Exception, match="403"):
        await executor.start(_spec_for("r-0"))
    assert "run-r-0" in api.secrets, "the Job exists and its pod needs the token"

    await executor.start(_spec_for("r-0"))  # the activity's retry
    api.ttl_collects("run-r-0")
    assert api.secrets == {}


async def test_a_job_that_cannot_be_created_leaves_no_token_behind() -> None:
    """Admission, quota or RBAC refuses the Job after the Secret was written: nothing will
    ever own that Secret, so it is deleted on the spot and the error still surfaces."""
    api = _ApiServer()
    api.refuse[("POST", "/jobs")] = 403
    with pytest.raises(Exception, match="403"):
        await KubernetesJobExecutor(client=api).start(_spec_for("r-0"))  # type: ignore[arg-type]
    assert api.jobs == {} and api.secrets == {}


async def test_a_job_created_despite_a_lost_answer_keeps_its_token() -> None:
    """The create reached the cluster but its answer was lost: the Job exists, its pod needs the
    Secret. The clean-up only deletes a Secret whose Job really does not exist."""

    class _LostAnswer(_ApiServer):
        async def request(self, method: str, path: str, **kwargs: Any) -> Any:
            answer = await super().request(method, path, **kwargs)
            if method == "POST" and path.endswith("/jobs"):
                raise TimeoutError("answer lost")
            return answer

    api = _LostAnswer()
    with pytest.raises(TimeoutError):
        await KubernetesJobExecutor(client=api).start(_spec_for("r-0"))  # type: ignore[arg-type]
    assert "run-r-0" in api.secrets


async def test_the_runner_pod_names_the_pull_secrets_it_is_given() -> None:
    """S51-09: a job step naming its own image (a private registry project) sat in
    ImagePullBackOff, its pod naming no pull secret. By name: the secret's value never enters it."""
    client = _RecordingClient()
    executor = KubernetesJobExecutor(client=client, image_pull_secrets=["harbor-prive", "ghcr-ee"])  # type: ignore[arg-type]
    await executor.start(_spec())
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    assert job["spec"]["template"]["spec"]["imagePullSecrets"] == [
        {"name": "harbor-prive"},
        {"name": "ghcr-ee"},
    ]


async def test_without_pull_secrets_the_pod_names_none() -> None:
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    assert "imagePullSecrets" not in job["spec"]["template"]["spec"]


def test_the_deployment_s_pull_secrets_reach_the_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the chart sets from `global.imagePullSecrets`, read where the executor is built."""
    from choregos_adapters import build

    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    monkeypatch.setenv("CHOREGOS_RUNNER_IMAGE_PULL_SECRETS", "harbor-prive, ghcr-ee,")
    executor = build("runtime", "k8s_job", {"kubernetes": {"token": "t", "verify": False}})
    assert executor.image_pull_secrets == ["harbor-prive", "ghcr-ee"]
