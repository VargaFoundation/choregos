"""S35-01 — the chart's `agent_sandbox` executor values, checked against the PINNED operator.

`runner.executor: agent_sandbox` renders a SandboxTemplate and a SandboxWarmPool (ADR 0016).
What is proved here, without a cluster:

- both objects fit the CRD schemas of the pinned release, field for field (a misspelt field
  is not refused by Kubernetes, it is pruned: here it is refused);
- the template's pod passes the strict Kyverno `non-root` rule, and the dedicated policy that
  judges the template itself (`infra/policies/sandbox-template.yaml`) carries the same pattern;
- a warm sandbox holds nothing: no service account token, no `envFrom` secret, no secret
  reference at all, and its environment is plain deployment-wide values;
- `runner.warmPool.size` and `runner.runtimeClass` reach the pool and the template;
- the pins agree: the kustomization, the fixture and the CRD versions name the same release.

What a cluster proves (`tests/cluster/test_agent_sandbox.py`): that the operator accepts these
objects and a Sandbox is really created and deleted.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
from types import ModuleType
from typing import Any

import pytest
import yaml

from .test_pod_security import violations

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
KUSTOMIZATION = ROOT / "infra" / "operators" / "agent-sandbox" / "kustomization.yaml"
PINNED_RELEASE = "v1.0.6"

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _crds() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "agent_sandbox_crds", ROOT / "tests" / "fixtures" / "agent_sandbox" / "crds.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(*overrides: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    args = ["helm", "template", "choregos", str(CHART), "--namespace", "choregos"]
    for override in overrides:
        args += ["--set", override]
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=120)


SANDBOX_VALUES = (
    "choregos-orchestrator.runner.executor=agent_sandbox",
    "choregos-orchestrator.runner.namespacePattern=choregos",
    "global.rbac.jobRunner=true",
)


def _objects(*overrides: str) -> dict[str, dict[str, Any]]:
    rendered = _render(*SANDBOX_VALUES, *overrides).stdout
    return {
        f"{doc['kind']}/{doc['metadata']['name']}": doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind")
    }


@needs_helm
def test_template_and_pool_fit_the_pinned_crds() -> None:
    objects = _objects("choregos-orchestrator.runner.warmPool.size=3")
    template = objects["SandboxTemplate/choregos-runner"]
    pool = objects["SandboxWarmPool/choregos-runner"]
    crds = _crds()
    assert crds.problems(template) == []
    assert crds.problems(pool) == []
    assert pool["spec"]["replicas"] == 3
    assert pool["spec"]["sandboxTemplateRef"]["name"] == "choregos-runner"


@needs_helm
def test_a_warm_sandbox_of_an_older_template_is_not_handed_to_a_run() -> None:
    """S35-02b: on dev, the pool kept a 0.18.1 sandbox after the template moved to 0.18.3, and
    the next claim would have run in it — the operator's default `OnReplenish` replaces a stale
    sandbox only once it is claimed. `Recreate` deletes it when the template changes."""
    pool = _objects()["SandboxWarmPool/choregos-runner"]
    assert pool["spec"]["updateStrategy"] == {"type": "Recreate"}
    crds = _crds()
    assert crds.problems(pool) == []
    pool["spec"]["updateStrategy"]["type"] = "Recreated"
    assert crds.problems(pool) != [], "the pinned CRD knows the strategy's values"


@needs_helm
def test_the_validator_does_refuse_a_pruned_field() -> None:
    """The check above is worth something only if a typo makes it fail."""
    template = _objects()["SandboxTemplate/choregos-runner"]
    template["spec"]["envVarInjectionPolicy"] = "Disallowed"
    assert any("envVarInjectionPolicy" in p for p in _crds().problems(template))


@needs_helm
def test_the_sandbox_pod_passes_the_kyverno_non_root_rule() -> None:
    pod = _objects()["SandboxTemplate/choregos-runner"]["spec"]["podTemplate"]["spec"]
    assert violations(pod) == []


def test_the_template_policy_carries_the_pod_rule() -> None:
    """Kyverno judges the controller's pods at admission (the `Pod` rule). The template
    policy refuses the mistake where it is written, with the SAME pattern."""
    pods = yaml.safe_load((ROOT / "infra" / "policies" / "pod-security.yaml").read_text())
    pod_rule = next(r for r in pods["spec"]["rules"] if r["name"] == "non-root")
    templates = yaml.safe_load((ROOT / "infra" / "policies" / "sandbox-template.yaml").read_text())
    rule = next(r for r in templates["spec"]["rules"] if r["name"] == "non-root")
    kinds = rule["match"]["any"][0]["resources"]["kinds"]
    assert "extensions.agents.x-k8s.io/v1beta1/SandboxTemplate" in kinds
    assert (
        rule["validate"]["pattern"]["spec"]["podTemplate"]["spec"] == pod_rule["validate"]["pattern"]["spec"]
    )
    assert templates["spec"]["validationFailureAction"] == "Enforce"
    listed = yaml.safe_load((ROOT / "infra" / "policies" / "kustomization.yaml").read_text())
    assert "sandbox-template.yaml" in listed["resources"]


@needs_helm
def test_a_warm_sandbox_holds_no_credential() -> None:
    """ADR 0016: a warm sandbox has never run anything and holds nothing until a run is
    assigned. A secret reference in the template would be in EVERY warm pod."""
    pod = _objects(
        "choregos-orchestrator.runner.envFromSecrets={git-credentials}",
        "choregos-orchestrator.runner.env.LOG_FORMAT=json",
    )["SandboxTemplate/choregos-runner"]["spec"]["podTemplate"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    text = yaml.safe_dump(pod)
    for forbidden in ("secretKeyRef", "secretRef", "envFrom", "git-credentials", "projected"):
        assert forbidden not in text, f"a warm sandbox would carry `{forbidden}` before any assignment"
    runner = pod["containers"][0]
    assert runner["args"][0] == "await-assignment"
    # The egress gate's report path (S80-07f) is a plain value too, and names no secret.
    assert {e["name"]: e["value"] for e in runner["env"]} == {
        "LOG_FORMAT": "json",
        "CHOREGOS_EGRESS_GATE_REPORT": "/egress-gate/report.json",
    }


@needs_helm
def test_runtime_class_and_storage_reach_the_template() -> None:
    template = _objects(
        "global.runner.runtimeClass=gvisor",
        "choregos-orchestrator.runner.sandbox.storage=20Gi",
    )["SandboxTemplate/choregos-runner"]
    assert template["spec"]["podTemplate"]["spec"]["runtimeClassName"] == "gvisor"
    claim = template["spec"]["volumeClaimTemplates"][0]
    assert claim["spec"]["resources"]["requests"]["storage"] == "20Gi"


@needs_helm
def test_the_executor_may_manage_claims_and_sandboxes() -> None:
    role = _objects()["Role/choregos-orchestrator-jobs"]
    rules = {(tuple(r["apiGroups"]), tuple(r["resources"])): set(r["verbs"]) for r in role["rules"]}
    assert {"create", "get", "delete", "patch"} <= rules[
        (("extensions.agents.x-k8s.io",), ("sandboxclaims",))
    ]
    assert {"get", "patch"} <= rules[(("agents.x-k8s.io",), ("sandboxes",))]


@needs_helm
def test_a_per_project_namespace_pattern_is_refused() -> None:
    """The template lives in the release namespace; claims made elsewhere would find no pool."""
    result = _render(
        "choregos-orchestrator.runner.executor=agent_sandbox",
        "choregos-orchestrator.runner.namespacePattern=proj-{slug}-runners",
        check=False,
    )
    assert result.returncode != 0
    assert "namespacePattern" in result.stderr


@needs_helm
def test_other_executors_render_no_sandbox() -> None:
    rendered = _render("global.runner.executor=tekton").stdout
    assert "SandboxTemplate" not in rendered and "agents.x-k8s.io" not in rendered


def test_the_pins_agree() -> None:
    """One release, named the same way in the GitOps source and in the schema fixture."""
    kustomization = yaml.safe_load(KUSTOMIZATION.read_text())
    (resource,) = kustomization["resources"]
    assert resource.endswith(f"//k8s?ref={PINNED_RELEASE}"), resource
    (image,) = kustomization["images"]
    assert image["digest"].startswith("sha256:") and "newTag" not in image, "pinned by digest"
    pinned = _crds().pinned()
    assert pinned["release"] == PINNED_RELEASE
    assert {k: v["version"] for k, v in pinned["kinds"].items()} == {
        "Sandbox": "v1beta1",
        "SandboxClaim": "v1beta1",
        "SandboxTemplate": "v1beta1",
        "SandboxWarmPool": "v1beta1",
    }


def _selects(selector: dict[str, Any], labels: dict[str, str]) -> bool:
    """`matchLabels` only — what these policies use. An empty selector selects every pod."""
    assert set(selector) <= {"matchLabels"}, f"unexpected selector form: {selector}"
    return (selector.get("matchLabels") or {}).items() <= labels.items()


@needs_helm
def test_only_the_orchestrator_executor_may_reach_the_assignment_port() -> None:
    """ADR 0053: a pod of the namespace must not push first and occupy a warm sandbox."""
    objects = _objects("choregos-orchestrator.runner.sandbox.assignmentPort=9876")
    sandbox_pod = objects["SandboxTemplate/choregos-runner"]["spec"]["podTemplate"]["metadata"]["labels"]
    executor_pod = objects["Deployment/choregos-orchestrator-executor"]["spec"]["template"]["metadata"][
        "labels"
    ]
    other_pods = [
        doc["spec"]["template"]["metadata"]["labels"]
        for name, doc in objects.items()
        if name.startswith("Deployment/") and name != "Deployment/choregos-orchestrator-executor"
    ]
    policy = objects["NetworkPolicy/choregos-runner-assignment"]["spec"]
    assert _selects(policy["podSelector"], sandbox_pod), "the policy selects the sandbox pods"
    assert not _selects(policy["podSelector"], executor_pod), "and not the orchestrator"
    assert policy["policyTypes"] == ["Ingress"]
    (rule,) = policy["ingress"]
    assert rule["ports"] == [{"protocol": "TCP", "port": 9876}]
    (peer,) = rule["from"]
    assert peer["namespaceSelector"] == {"matchLabels": {"kubernetes.io/metadata.name": "choregos"}}
    assert _selects(peer["podSelector"], executor_pod), "the orchestrator's executor workers get in"
    assert not any(_selects(peer["podSelector"], labels) for labels in other_pods), "no other pod does"


@needs_helm
def test_no_other_policy_reopens_the_sandbox_pods() -> None:
    """NetworkPolicies add up: one broader ingress rule on the sandbox pods undoes the one above.
    `choregos-allow-ingress` admits the whole namespace to `app.kubernetes.io/part-of: choregos`
    pods — which is why the sandbox pods do not carry that label."""
    objects = _objects("networkPolicies.enabled=true")
    sandbox_pod = objects["SandboxTemplate/choregos-runner"]["spec"]["podTemplate"]["metadata"]["labels"]
    assert "app.kubernetes.io/part-of" not in sandbox_pod
    ingress_on_sandbox = sorted(
        name
        for name, doc in objects.items()
        if doc["kind"] == "NetworkPolicy"
        and "Ingress" in doc["spec"].get("policyTypes", [])
        and _selects(doc["spec"]["podSelector"], sandbox_pod)
    )
    assert ingress_on_sandbox == ["NetworkPolicy/choregos-runner-assignment"], ingress_on_sandbox


@needs_helm
def test_a_platform_that_owns_network_policies_can_leave_the_assignment_one_out() -> None:
    """S35-05 — where an Argo CD AppProject blacklists NetworkPolicy (some platforms' tenants), one
    in the chart makes the whole sync fail. The switch drops that one object, and only it."""
    kept = _objects()
    dropped = _objects("choregos-orchestrator.runner.sandbox.assignmentNetworkPolicy=false")
    assert "NetworkPolicy/choregos-runner-assignment" in kept, "rendered by default"
    assert "NetworkPolicy/choregos-runner-assignment" not in dropped
    assert set(kept) - set(dropped) == {"NetworkPolicy/choregos-runner-assignment"}


@needs_helm
def test_the_executor_is_told_how_long_a_finished_sandbox_keeps_its_volume() -> None:
    def ttl(*overrides: str) -> str:
        deployment = _objects(*overrides)["Deployment/choregos-orchestrator-executor"]
        (container,) = deployment["spec"]["template"]["spec"]["containers"]
        return next(e["value"] for e in container["env"] if e["name"] == "CHOREGOS_SANDBOX_TTL_SECONDS")

    assert int(ttl()) <= 600, "not an hour of a volume per finished stage"
    assert ttl("choregos-orchestrator.runner.sandbox.ttlSecondsAfterFinished=120") == "120"


# ADR 0146: the agent in its own container ───────────────────────────────────────────────


def _pod(*overrides: str) -> dict[str, Any]:
    return _objects(*overrides)["SandboxTemplate/choregos-runner"]["spec"]["podTemplate"]["spec"]


SEPARATE = "global.runner.agentIsolation=container"


def _agent(pod: dict[str, Any]) -> dict[str, Any]:
    """The ADR 0146 agent container, behind the egress gate (S80-07f), which comes first."""
    [agent] = [c for c in pod["initContainers"] if c["name"] == "agent"]
    return agent


@needs_helm
def test_by_default_the_agent_shares_the_runners_container() -> None:
    """The rollback: `process` (the default) renders the pod this chart rendered before ADR 0146,
    with only the egress gate (S80-07f) before the runner."""
    pod = _pod()
    assert [c["name"] for c in pod["initContainers"]] == ["egress-gate"]
    assert [c["name"] for c in pod["containers"]] == ["runner"]
    assert [v["name"] for v in pod["volumes"]] == ["egress-gate"]
    names = {e["name"] for e in pod["containers"][0].get("env", [])}
    assert "CHOREGOS_RUNNER_AGENT_ISOLATION" not in names


@needs_helm
def test_the_separate_agent_is_a_sidecar_with_another_uid_and_no_secret() -> None:
    pod = _pod(SEPARATE, "choregos-orchestrator.runner.envFromSecrets={git-credentials}")
    agent = _agent(pod)
    runner = pod["containers"][0]
    assert agent["name"] == "agent"
    assert agent["restartPolicy"] == "Always", "a native sidecar: Ready before the runner starts"
    assert agent["args"][0] == "launcher"
    assert agent["securityContext"]["runAsUser"] not in (None, 1000), "the runner's uid is not the agent's"
    assert agent["securityContext"]["readOnlyRootFilesystem"] is True
    assert agent["securityContext"]["runAsNonRoot"] is True
    assert agent["securityContext"]["allowPrivilegeEscalation"] is False
    assert agent["securityContext"]["capabilities"]["drop"] == ["ALL"]
    # Nothing of the runner's: no secret, no envFrom, no environment but the two paths.
    assert {e["name"] for e in agent["env"]} == {"TMPDIR", "HOME"}
    for forbidden in ("secretKeyRef", "secretRef", "envFrom", "git-credentials", "projected"):
        assert forbidden not in yaml.safe_dump(agent)
    assert pod["automountServiceAccountToken"] is False
    # The runner says where the agent is, and refuses to start it itself.
    runner_env = {e["name"]: e["value"] for e in runner["env"]}
    assert runner_env["CHOREGOS_RUNNER_AGENT_ISOLATION"] == "container"
    assert runner_env["TMPDIR"] == "/exchange"
    assert runner_env["CHOREGOS_RUNNER_LAUNCHER_PORT"] == agent["args"][-1]
    assert violations(pod) == []


@needs_helm
def test_the_two_containers_share_the_workspace_and_the_exchange_directory_only() -> None:
    pod = _pod(SEPARATE)
    agent = _agent(pod)
    runner = pod["containers"][0]
    mounts = {c["name"]: {m["name"]: m["mountPath"] for m in c["volumeMounts"]} for c in (agent, runner)}
    shared = set(mounts["agent"]) & set(mounts["runner"])
    assert shared == {"workspace", "exchange"}, mounts
    assert mounts["agent"]["workspace"] == mounts["runner"]["workspace"] == "/workspace"
    # The exchange directory is an emptyDir of the pod: gone with it, never a claim.
    assert {"name": "exchange", "emptyDir": {}} in pod["volumes"]


@needs_helm
def test_the_separate_agent_template_fits_the_pinned_crds() -> None:
    template = _objects(SEPARATE)["SandboxTemplate/choregos-runner"]
    assert _crds().problems(template) == []


@needs_helm
def test_an_unknown_isolation_is_refused() -> None:
    result = _render(*SANDBOX_VALUES, "global.runner.agentIsolation=shared", check=False)
    assert result.returncode != 0
    assert "agentIsolation" in result.stderr


@needs_helm
@pytest.mark.parametrize("value", ["process", "container"])
def test_the_former_sandbox_value_is_refused_with_where_it_went(value: str) -> None:
    """S65-04d: `runner.sandbox.agentIsolation` (0.27.6 to 0.27.15) moved to `global.runner.agentIsolation`.
    Ignored, a `container` there would leave the agent beside the runner without a word."""
    result = _render(
        *SANDBOX_VALUES, f"choregos-orchestrator.runner.sandbox.agentIsolation={value}", check=False
    )
    assert result.returncode != 0
    assert "moved to global.runner.agentIsolation" in result.stderr


@needs_helm
@pytest.mark.parametrize("isolation", ["process", "container"])
def test_the_api_and_the_workers_read_the_same_isolation_as_the_template(isolation: str) -> None:
    """One value (ADR 0146 §9): the API judges `/readiness` and the workers refuse with what the template
    renders; a worker told `process` beside a `container` pool would refuse nothing it should."""
    objects = _objects(f"global.runner.agentIsolation={isolation}")
    for name in ("Deployment/choregos-api", "Deployment/choregos-orchestrator-executor"):
        (container, *_) = objects[name]["spec"]["template"]["spec"]["containers"]
        env = {e["name"]: e.get("value") for e in container["env"]}
        assert env["CHOREGOS_RUNNER_AGENT_ISOLATION"] == isolation, name
    pod = objects["SandboxTemplate/choregos-runner"]["spec"]["podTemplate"]["spec"]
    assert ("agent" in {c["name"] for c in pod["initContainers"]}) is (isolation == "container")


@needs_helm
def test_the_launcher_probe_is_an_exec_inside_the_pod() -> None:
    """The kubelet's TCP probe connects from the node: it cannot reach a loopback listener."""
    agent = _agent(_pod(SEPARATE))
    probe = agent["startupProbe"]
    assert "tcpSocket" not in probe
    assert probe["exec"]["command"][0] == "python"


@needs_helm
def test_the_runner_container_requests_its_own_beside_the_agent() -> None:
    """ADR 0146 §12: under `container` the runner's container requests its own, small; under `process`
    the single container keeps what it had."""
    runner = _pod(SEPARATE)["containers"][0]
    assert runner["resources"] == {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "1", "memory": "2Gi"},
    }
    assert _pod()["containers"][0]["resources"]["requests"] == {"cpu": "500m", "memory": "1Gi"}


@needs_helm
def test_the_jobs_agent_container_is_the_templates() -> None:
    """One shape (S65-04h): the `k8s_job` pod's agent container is the warm pool's, but for the image and
    the resources, and passes the same admission rule."""
    from tranibor_adapters.executor.k8s_job import KubernetesJobExecutor
    from tranibor_core.domain import StageJobSpec

    spec = StageJobSpec(
        run_id="r-1",
        project_slug="demo",
        namespace="choregos",
        runner_image="registry.example/choregos-runner:1.0",
        api_url="http://choregos-api:8000",
        run_token="token",
        agent_isolation="container",
    )
    job = KubernetesJobExecutor(client=object())._job(spec, "run-r-1")  # type: ignore[arg-type]
    job_pod = job["spec"]["template"]["spec"]
    assert violations(job_pod) == []
    job_agent = next(c for c in job_pod["initContainers"] if c["name"] == "agent")
    template_agent = _agent(_pod(SEPARATE))
    keys = ("restartPolicy", "args", "env", "securityContext", "volumeMounts")
    assert {k: job_agent[k] for k in keys} == {k: template_agent[k] for k in keys}


@needs_helm
def test_the_workers_get_the_runners_own_resources() -> None:
    deployment = _objects()["Deployment/choregos-orchestrator-executor"]
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert yaml.safe_load(env["CHOREGOS_RUNNER_OWN_RESOURCES"]) == {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "1", "memory": "2Gi"},
    }
