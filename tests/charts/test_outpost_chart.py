"""The chart `choregos-outpost`: what an outpost's operator installs in their own cluster
(S65-16, ADR 0147, ADR 0148 §13, ADR 0149 §5 and §9).

It is not a subchart of the platform's and shares no value with it. It runs the daemon with one
identity and one journal per instance, locked down as a plugin server's pod is (ADR 0096 §2); renders
the policy file the daemon reads, without ever holding a credential; enrols an instance once; and
lets its pods reach the door and the servers its endpoints name, and nothing reach them.

Each test renders the chart with `helm template` and reads the objects. Where the chart writes
something the daemon reads, the daemon's own code reads it back: its policy loader, its command
line. The enrolment script is run, with a stand-in for the daemon.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tomllib
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos-outpost"
EXAMPLE = CHART / "values" / "example.yaml"


def _door_key(label: str) -> str:
    """The public half of an Ed25519 key derived from a label, as the contract writes a key: no key
    is written in this file."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(label.encode()).digest())
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


#: A door's public key, and the next one of a rotation.
DOOR_KEY = _door_key("choregos tests: the door's signing key an outpost pins")
NEXT_KEY = _door_key("choregos tests: the door's next signing key, during a rotation")
MINIMAL = (
    'policy.control_plane="https://outposts.example.com"',
    f'policy.door_keys=["{DOOR_KEY}"]',
)
TRACKER = (
    'policy.endpoints.tracker={"url":"http://tracker-mcp.tools.svc:8080/mcp","tools":["search_*"],'
    '"auth":{"secretName":"tracker-mcp-token","secretKey":"token"}}'
)
STATE, AUDIT, CONFIG = "/var/lib/choregos-outpost", "/var/log/choregos-outpost", "/etc/choregos-outpost"
PRIVATE = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10"]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*json_sets: str, values: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "factory-floor", str(CHART), "--namespace", "outposts"]
    if values is not None:
        command += ["-f", str(values)]
    for value in json_sets:
        command += ["--set-json", value]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _documents(*json_sets: str, values: pathlib.Path | None = None) -> list[dict[str, Any]]:
    rendered = _render(*json_sets, values=values)
    assert rendered.returncode == 0, rendered.stderr[-800:]
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _refusal(*json_sets: str) -> str:
    rendered = _render(*json_sets)
    assert rendered.returncode != 0, "the chart rendered what it must refuse"
    return rendered.stderr


def _one(documents: list[dict[str, Any]], kind: str, name: str | None = None) -> dict[str, Any]:
    (found,) = [d for d in documents if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]
    return found


def _daemon(documents: list[dict[str, Any]]) -> dict[str, Any]:
    return _one(documents, "StatefulSet")


def _pod(documents: list[dict[str, Any]]) -> dict[str, Any]:
    pod: dict[str, Any] = _daemon(documents)["spec"]["template"]["spec"]
    return pod


def _env(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in container.get("env") or []}


def _policy(documents: list[dict[str, Any]]) -> dict[str, Any]:
    config = _one(documents, "ConfigMap")
    assert list(config["data"]) == ["policy.yaml"]
    loaded: dict[str, Any] = yaml.safe_load(config["data"]["policy.yaml"])
    return loaded


# ───────────────────────── not the platform's ─────────────────────────


def test_it_is_a_chart_of_its_own_at_the_repositorys_version() -> None:
    """Not a subchart of the platform's, no dependency on it, no value shared with it; and one version
    everywhere, which the release checks before it tags (`tools/bump_version.py`)."""
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))
    assert chart["name"] == "choregos-outpost" and "dependencies" not in chart
    platform = yaml.safe_load((ROOT / "charts/choregos/Chart.yaml").read_text(encoding="utf-8"))
    assert "choregos-outpost" not in {dependency["name"] for dependency in platform["dependencies"]}
    assert not (ROOT / "charts/choregos/charts/outpost").exists()
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert "global" not in values, "`global` is how a parent chart reaches into a subchart"
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert chart["version"] == version and chart["appVersion"] == version

    import importlib.util

    spec = importlib.util.spec_from_file_location("bump_version", ROOT / "tools" / "bump_version.py")
    assert spec and spec.loader
    bump = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bump)
    assert CHART / "Chart.yaml" in [path for path, _ in bump._files()], "a release would leave it behind"


def test_the_image_is_the_release_one_pinned_by_digest_when_given() -> None:
    digest = "sha256:" + "ab" * 32
    container = _pod(_documents(*MINIMAL))["containers"][0]
    version = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))["appVersion"]
    # The image the release builds, signs and attests (`release.yml`, the `outpost` image).
    assert container["image"] == f"ghcr.io/vargafoundation/choregos-outpost:{version}"
    pinned = _pod(_documents(*MINIMAL, f'image.digest="{digest}"', 'image.tag="9.9.9"'))
    for holder in (pinned["containers"][0], pinned["initContainers"][0]):
        assert holder["image"] == f"ghcr.io/vargafoundation/choregos-outpost@{digest}"
    assert "image.digest" in _refusal(*MINIMAL, 'image.digest="latest"')
    # `run` is what the pod runs, through the image's own entrypoint.
    assert container["args"] == ["run"] and "command" not in container


# ───────────────────────── locked down as a plugin server's pod is ─────────────────────────


def _model() -> dict[str, Any]:
    """A plugin server's pod as the platform writes it (ADR 0096 §2): the model."""
    from choregos_adapters.plugin_servers import ServerDeployment, server_manifests

    model = ServerDeployment(
        name="s", namespace="n", image="registry.example.com/i@sha256:" + "0" * 64, port=8080,
        health_path="/healthz", env={}, labels={},
    )  # fmt: skip
    (deployment,) = [m for m in server_manifests(model) if m["kind"] == "Deployment"]
    pod: dict[str, Any] = deployment["spec"]["template"]["spec"]
    return pod


def _locked_down_as_the_model(pod: dict[str, Any], containers: list[dict[str, Any]]) -> None:
    model = _model()
    assert pod["automountServiceAccountToken"] is model["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is model["enableServiceLinks"] is False
    for field in ("runAsNonRoot", "seccompProfile"):
        assert pod["securityContext"][field] == model["securityContext"][field], field
    assert pod["securityContext"]["runAsUser"] != 0 and pod["securityContext"]["runAsGroup"] != 0
    assert containers, "no container to check"
    for container in containers:
        # Not root, a read-only root filesystem, every capability dropped, no escalation, seccomp.
        assert container["securityContext"] == model["containers"][0]["securityContext"], container["name"]
        assert set(container["resources"]) == {"requests", "limits"}, container["name"]
        assert set(container["resources"]["limits"]) >= {"cpu", "memory"}


def test_the_pod_is_locked_down_as_a_plugin_servers_is() -> None:
    documents = _documents(values=EXAMPLE)
    pod = _pod(documents)
    _locked_down_as_the_model(pod, [*pod["initContainers"], *pod["containers"]])
    assert [c["name"] for c in pod["containers"]] == ["outpost"]
    assert [c["name"] for c in pod["initContainers"]] == ["enrol"]
    # The daemon listens on nothing: no port, no probe, and no Service of the chart selects it.
    daemon = pod["containers"][0]
    assert not {"ports", "readinessProbe", "livenessProbe", "startupProbe"} & set(daemon)
    labels = _daemon(documents)["spec"]["template"]["metadata"]["labels"]
    for service in (d for d in documents if d["kind"] == "Service"):
        assert not service["spec"]["selector"].items() <= labels.items(), service["metadata"]["name"]


def test_the_service_account_is_bound_to_no_role_and_its_token_is_not_mounted() -> None:
    documents = _documents(values=EXAMPLE)
    account = _one(documents, "ServiceAccount")
    assert account["automountServiceAccountToken"] is False
    assert _pod(documents)["serviceAccountName"] == account["metadata"]["name"]
    assert not [
        d["kind"]
        for d in documents
        if d["kind"] in {"Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding"}
    ], "the daemon asks nothing of the cluster it runs in"
    # Whatever the account's default, no pod of the chart mounts a token.
    for workload in (d for d in documents if d["kind"] in {"StatefulSet", "Deployment"}):
        assert workload["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


def test_only_the_state_and_the_audit_log_are_writable() -> None:
    documents = _documents(values=EXAMPLE)
    pod = _pod(documents)
    (daemon,) = pod["containers"]
    writable = {m["mountPath"]: m for m in daemon["volumeMounts"] if not m.get("readOnly")}
    assert set(writable) == {STATE, AUDIT}
    assert {m["name"] for m in writable.values()} == {"data"}, "both are on the instance's own claim"
    assert {m["subPath"] for m in writable.values()} == {"state", "audit"}
    # No scratch space: nothing else of the filesystem can be written, `/tmp` included.
    assert not [v["name"] for v in pod["volumes"] if "emptyDir" in v]
    assert daemon["securityContext"]["readOnlyRootFilesystem"] is True
    # The audit log is where the policy says it is.
    assert pathlib.PurePosixPath(_policy(documents)["audit"]["file"]).parent.as_posix() == AUDIT
    elsewhere = _pod(_documents(*MINIMAL, 'policy.audit.file="/srv/audit/outpost.jsonl"'))["containers"][0]
    assert {m["mountPath"] for m in elsewhere["volumeMounts"] if not m.get("readOnly")} == {
        STATE,
        "/srv/audit",
    }
    assert "policy.audit.file" in _refusal(*MINIMAL, 'policy.audit.file="/audit.jsonl"')


# ───────────────────────── one identity and one journal per instance ─────────────────────────


def test_each_instance_has_a_volume_of_its_own() -> None:
    """A second daemon on the same volume refuses to start, and two daemons on one identity is what
    the door takes for a stolen key: replicas each get a claim, from a claim template."""
    for replicas in (1, 3):
        documents = _documents(*MINIMAL, f"replicas={replicas}")
        daemon = _daemon(documents)
        assert daemon["spec"]["replicas"] == replicas
        (claim,) = daemon["spec"]["volumeClaimTemplates"]
        assert claim["metadata"]["name"] == "data"
        assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
        assert claim["spec"]["resources"]["requests"]["storage"] == "1Gi"
        # The state is the claim's and nothing else's: no volume shared by the pods carries it.
        pod = daemon["spec"]["template"]["spec"]
        assert "data" not in {volume["name"] for volume in pod["volumes"]}
        assert not [v["name"] for v in pod["volumes"] if "persistentVolumeClaim" in v]
        assert not [d["kind"] for d in documents if d["kind"] in {"Deployment", "PersistentVolumeClaim"}]
    # The kubelet sets the volume's group once: at every start it would make the keys group-writable,
    # which the daemon refuses (`identity.py`).
    assert _pod(_documents(*MINIMAL))["securityContext"]["fsGroupChangePolicy"] == "OnRootMismatch"
    assert (
        _daemon(_documents(*MINIMAL, 'persistence.storageClass="fast"'))["spec"]["volumeClaimTemplates"][0][
            "spec"
        ]["storageClassName"]
        == "fast"
    )


# ───────────────────────── the policy file ─────────────────────────


def test_the_policy_the_chart_renders_is_one_the_daemon_starts_on(tmp_path: pathlib.Path) -> None:
    """Read back by the daemon's own loader: the contract's schema, its model, and the keys."""
    import choregos_contracts as contracts
    from choregos_outpost import policy as loader

    documents = _documents(values=EXAMPLE)
    path = tmp_path / "policy.yaml"
    path.write_text(_one(documents, "ConfigMap")["data"]["policy.yaml"], encoding="utf-8")
    loaded = loader.load(path)
    assert loaded.control_plane == "https://outposts.example.com"
    # The example pins the public example key of the platform's contracts, and no other.
    contract = yaml.safe_load(
        (contracts.schemas_dir() / "examples" / "outpost-policy.example.yaml").read_text()
    )
    assert loaded.door_keys == contract["door_keys"] and loaded.concurrency == 8
    assert loaded.audit.file == f"{AUDIT}/audit.jsonl"
    tracker = loaded.endpoints["tracker"]
    assert tracker.url == "http://tracker-mcp.tools.svc:8080/mcp"
    assert tracker.tools == ["search_*", "get_*", "create_issue"]
    assert (tracker.writes, tracker.timeout_s, tracker.max_payload_kb) == ("allow", 60, 512)
    assert tracker.auth is not None and tracker.auth.header == "Authorization"
    assert loaded.endpoints["wiki"].auth is None
    # With nothing but what is required, the daemon's own defaults apply.
    minimal = tmp_path / "minimal.yaml"
    rotation = f'policy.door_keys=["{DOOR_KEY}","{NEXT_KEY}"]'
    minimal.write_text(
        _one(_documents(*MINIMAL, rotation), "ConfigMap")["data"]["policy.yaml"], encoding="utf-8"
    )
    during = loader.load(minimal)
    assert during.endpoints == {} and during.door_keys == [DOOR_KEY, NEXT_KEY]


def test_a_credential_is_a_mounted_secret_and_never_a_value_of_the_config_map() -> None:
    documents = _documents(values=EXAMPLE)
    policy = _policy(documents)
    from_file = policy["endpoints"]["tracker"]["auth"]["from_file"]
    assert from_file == f"{CONFIG}/credentials/tracker/token"
    assert set(policy["endpoints"]["tracker"]["auth"]) == {"from_file", "header"}
    # The chart's own keys are not the policy file's: the Secret's name, the network's shape.
    text = _one(documents, "ConfigMap")["data"]["policy.yaml"]
    for chart_only in ("secretName", "secretKey", "tracker-mcp-token", "network"):
        assert chart_only not in text, chart_only
    # The file the policy names is the Secret's key, mounted read-only in the daemon's container.
    pod = _pod(documents)
    (volume,) = [v for v in pod["volumes"] if v["name"] == "credentials"]
    assert volume["projected"]["sources"] == [
        {"secret": {"name": "tracker-mcp-token", "items": [{"key": "token", "path": "tracker/token"}]}}
    ]
    (mount,) = [m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "credentials"]
    assert mount == {"name": "credentials", "mountPath": f"{CONFIG}/credentials", "readOnly": True}
    assert (
        f"{mount['mountPath']}/{volume['projected']['sources'][0]['secret']['items'][0]['path']}" == from_file
    )
    # No Secret is rendered by the chart: every one is the operator's.
    assert not [d for d in documents if d["kind"] == "Secret"]
    # Without a credential, no volume for one.
    assert "credentials" not in {v["name"] for v in _pod(_documents(*MINIMAL))["volumes"]}


def _endpoint(alias: str = "t", **fields: Any) -> str:
    """An endpoint of the policy as a `--set-json` value: a URL and no tool, plus or minus `fields`."""
    endpoint = {"url": "http://t.tools.svc/mcp", "tools": []} | fields
    return f"policy.endpoints.{alias}=" + json.dumps({k: v for k, v in endpoint.items() if v is not None})


@pytest.mark.parametrize(
    ("sets", "names"),
    [
        ((MINIMAL[1],), "policy.control_plane is required"),
        ((MINIMAL[0],), "policy.door_keys is required"),
        ((MINIMAL[1], 'policy.control_plane="https://outposts.example.com/outposts/v1"'), "not an origin"),
        ((MINIMAL[0], 'policy.door_keys=["SHA256:abc"]'), "policy.door_keys"),
        ((MINIMAL[0], f'policy.door_keys=["{DOOR_KEY}","{DOOR_KEY}"]'), "pinned once"),
        ((*MINIMAL, "policy.concurrency=65"), "policy.concurrency"),
        ((*MINIMAL, 'policy.projects=["billing"]'), "policy.projects"),
        ((*MINIMAL, _endpoint("Tracker")), "policy.endpoints.Tracker"),
        ((*MINIMAL, _endpoint(url=None)), "policy.endpoints.t.url is required"),
        ((*MINIMAL, _endpoint(tools=None)), "policy.endpoints.t.tools is required"),
        ((*MINIMAL, _endpoint(writes="yes")), "policy.endpoints.t.writes"),
        ((*MINIMAL, _endpoint(timeout_s=0)), "policy.endpoints.t.timeout_s"),
        ((*MINIMAL, _endpoint(max_payload_kb=2048)), "policy.endpoints.t.max_payload_kb"),
        ((*MINIMAL, _endpoint(secret="x")), "policy.endpoints.t.secret"),
    ],
    ids=[
        "no control plane",
        "no door keys",
        "a control plane with a path",
        "a fingerprint for a key",
        "a key twice",
        "concurrency",
        "a rule on an asserted name",
        "an alias in capitals",
        "no url",
        "no tools",
        "writes",
        "timeout",
        "payload",
        "an unknown field",
    ],
)
def test_a_policy_the_daemon_would_not_start_on_fails_the_render(sets: tuple[str, ...], names: str) -> None:
    """The daemon does not start on an invalid policy. What the chart can check is refused at render,
    under the field's name, instead of found in a pod that never starts."""
    assert names in _refusal(*sets)


@pytest.mark.parametrize(
    "auth",
    [
        '{"token":"a-credential-typed-in-the-values"}',
        '{"from_file":"/secrets/token"}',
        '{"secretName":"tracker-mcp-token","secretKey":"token","value":"x"}',
        '{"secretName":"tracker-mcp-token"}',
    ],
    ids=["a value", "a path of the operator's", "a value beside the secret", "no key"],
)
def test_a_credential_typed_in_the_values_fails_the_render(auth: str) -> None:
    refusal = _refusal(*MINIMAL, _endpoint(auth=json.loads(auth)))
    assert "policy.endpoints.t.auth" in refusal and "secretName" in refusal


def test_a_change_of_the_policy_restarts_the_pods() -> None:
    """The daemon reads its policy when it starts and never again."""

    def checksum(*sets: str) -> str:
        annotations = _daemon(_documents(*MINIMAL, *sets))["spec"]["template"]["metadata"]["annotations"]
        return str(annotations["checksum/policy"])

    assert checksum() == checksum()
    assert checksum() != checksum("policy.concurrency=2")
    assert checksum() != checksum(TRACKER)
    # A rotation of the door's keys is a change of the policy.
    assert checksum() != checksum(f'policy.door_keys=["{DOOR_KEY}","{NEXT_KEY}"]')
    # What is not the policy restarts nothing by itself.
    assert checksum() == checksum('networkPolicy.controlPlane.cidrs=["203.0.113.10/32"]')


# ───────────────────────── enrolment: once ─────────────────────────


def _enrol(documents: list[dict[str, Any]]) -> dict[str, Any]:
    (container,) = _pod(documents)["initContainers"]
    return dict(container)


def test_the_token_comes_from_the_operators_secret_and_only_the_enrolment_sees_it() -> None:
    documents = _documents(*MINIMAL)
    pod, enrol = _pod(documents), _enrol(documents)
    (volume,) = [v for v in pod["volumes"] if v["name"] == "enrolment"]
    # Optional: once the instance is enrolled the token is spent, and its Secret can be deleted.
    assert volume["secret"]["secretName"] == "factory-floor-choregos-outpost-enrolment"
    assert volume["secret"]["optional"] is True
    assert _env(enrol)["ENROLMENT_TOKEN_FILE"]["value"] == f"{CONFIG}/enrolment/token"
    assert {"name": "enrolment", "mountPath": f"{CONFIG}/enrolment", "readOnly": True} in enrol[
        "volumeMounts"
    ]
    assert "enrolment" not in {m["name"] for m in pod["containers"][0]["volumeMounts"]}
    # Never a value: not in the environment, not in the ConfigMap.
    for container in (enrol, pod["containers"][0]):
        assert "CHOREGOS_OUTPOST_ENROLMENT_TOKEN" not in _env(container)
    named = _documents(*MINIMAL, 'enrolment={"existingSecret":"my-token","key":"t"}')
    (volume,) = [v for v in _pod(named)["volumes"] if v["name"] == "enrolment"]
    assert volume["secret"]["secretName"] == "my-token"
    assert _env(_enrol(named))["ENROLMENT_TOKEN_FILE"]["value"] == f"{CONFIG}/enrolment/t"
    # The enrolment writes the identity where the daemon reads it: the same claim, the same place.
    assert {"name": "data", "mountPath": STATE, "subPath": "state"} in enrol["volumeMounts"]
    assert {"name": "data", "mountPath": STATE, "subPath": "state"} in pod["containers"][0]["volumeMounts"]


@pytest.fixture
def enrolment(tmp_path: pathlib.Path) -> Any:
    """Runs the init container's script as the pod would, in a directory of the test, with a stand-in
    for the daemon that records how it was called."""
    enrol = _enrol(_documents(*MINIMAL))
    assert enrol["command"] == ["/bin/sh", "-c"]
    (script,) = enrol["args"]
    state, tools, calls = tmp_path / "state", tmp_path / "bin", tmp_path / "calls"
    tools.mkdir()
    (state / "identity").mkdir(parents=True)
    stand_in = tools / "choregos-outpost"
    stand_in.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n', encoding="utf-8")
    stand_in.chmod(0o755)
    token = tmp_path / "token"

    def run() -> tuple[subprocess.CompletedProcess[str], list[str]]:
        done = subprocess.run(
            ["/bin/sh", "-c", script],
            env={
                "PATH": f"{tools}:{os.environ['PATH']}",
                "CHOREGOS_OUTPOST_STATE_DIR": str(state),
                "ENROLMENT_TOKEN_FILE": str(token),
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return done, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []

    run.identity, run.token = state / "identity", token  # type: ignore[attr-defined]
    return run


def test_an_instance_without_an_identity_is_enrolled_with_the_token(enrolment: Any) -> None:
    enrolment.token.write_text("a-token-of-the-console\n", encoding="utf-8")
    done, calls = enrolment()
    assert done.returncode == 0, done.stderr
    assert calls == [f"enrol --token-file {enrolment.token}"]


def test_an_instance_that_already_enrolled_is_left_alone(enrolment: Any) -> None:
    """`enrol` always presents a token, and a token is spent by its first use: the script looks first.
    A restart of an enrolled instance must not need a token, nor present a spent one."""
    (enrolment.identity / "instance.json").write_text("{}", encoding="utf-8")
    for token in (None, "a-token-already-spent"):
        if token:
            enrolment.token.write_text(token, encoding="utf-8")
        done, calls = enrolment()
        assert done.returncode == 0 and calls == [], (done.stderr, calls)
        assert "already enrolled" in done.stdout


def test_keys_whose_enrolment_answer_was_lost_enrol_again(enrolment: Any) -> None:
    """Keys and no `instance.json`: the daemon enrols again with the keys it has (ADR 0148 §3)."""
    (enrolment.identity / "signing.key").write_text("k", encoding="utf-8")
    enrolment.token.write_text("a-fresh-token", encoding="utf-8")
    done, calls = enrolment()
    assert done.returncode == 0 and calls == [f"enrol --token-file {enrolment.token}"]


def test_without_an_identity_and_without_a_token_the_instance_waits_and_says_how(enrolment: Any) -> None:
    for content in (None, ""):
        if content is not None:
            enrolment.token.write_text(content, encoding="utf-8")
        done, calls = enrolment()
        assert done.returncode == 1 and calls == []
        assert "no enrolment token" in done.stderr
        assert (
            "kubectl -n outposts create secret generic factory-floor-choregos-outpost-enrolment"
            in done.stderr
        )
        assert "--from-literal=token=" in done.stderr


def test_the_keys_are_put_back_to_the_mode_the_daemon_accepts(enrolment: Any) -> None:
    """The kubelet's `fsGroup` pass makes every file of a volume group-writable, and the daemon
    refuses a private key its group can write: the daemon's own check reads the result."""
    from choregos_outpost import identity

    made = identity.create(enrolment.identity.parent / "fresh")
    for name in (identity.SIGNING_FILE, identity.SEALING_FILE):
        key = enrolment.identity / name
        key.write_bytes((enrolment.identity.parent / "fresh" / name).read_bytes())
        key.chmod(0o660)
    with pytest.raises(identity.IdentityError, match="open to others"):
        identity.load(enrolment.identity)
    (enrolment.identity / "instance.json").write_text("{}", encoding="utf-8")
    done, _ = enrolment()
    assert done.returncode == 0, done.stderr
    for name in (identity.SIGNING_FILE, identity.SEALING_FILE):
        assert stat.S_IMODE((enrolment.identity / name).stat().st_mode) == 0o600
    assert identity.load(enrolment.identity).fingerprint == made.fingerprint


def test_the_notes_say_how_to_read_the_fingerprint_and_what_restarts_on_purpose() -> None:
    """`helm template` does not print a chart's notes: their text is read as it is written."""
    notes = (CHART / "templates" / "NOTES.txt").read_text(encoding="utf-8")
    assert 'logs {{ include "choregos-outpost.fullname" . }}-0 -c enrol' in notes
    assert "outpost.pending" in notes and "pending until a person approves it" in notes
    assert "restart in a loop ON PURPOSE" in notes
    purposely = notes.split("ON PURPOSE:", 1)[1].split("The others:", 1)[0]
    assert "  3  " in purposely and "protocol" in purposely
    assert "  4  " in purposely and "revoked" in purposely


# ───────────────────────── what the daemon is told, in its own words ─────────────────────────


def _daemon_options() -> dict[str, str]:
    """Every environment variable the daemon's command line reads, by the option that reads it."""
    import typer
    from choregos_outpost.cli import app

    command = typer.main.get_command(app)
    found: dict[str, str] = {}
    groups = [command, *command.commands.values()]  # type: ignore[attr-defined]
    for group in groups:
        for parameter in group.params:
            variable = getattr(parameter, "envvar", None)
            if variable:
                found[str(variable)] = parameter.opts[0]
    return found


def test_every_variable_the_chart_sets_is_one_the_daemon_reads() -> None:
    known = _daemon_options()
    assert known["CHOREGOS_OUTPOST_GRACE_S"] == "--grace" and "CHOREGOS_OUTPOST_POLICY" in known
    documents = _documents(
        *MINIMAL, 'proxy.url="http://proxy.example.com:3128"', 'ca.configMap="house-ca"',
        'networkPolicy.controlPlane.cidrs=["203.0.113.7/32"]',
    )  # fmt: skip
    pod = _pod(documents)
    for container in (pod["initContainers"][0], pod["containers"][0]):
        ours = {name for name in _env(container) if name.startswith("CHOREGOS_OUTPOST_")}
        assert ours and ours <= set(known), ours - set(known)
    env = _env(pod["containers"][0])
    assert env["CHOREGOS_OUTPOST_POLICY"]["value"] == f"{CONFIG}/policy/policy.yaml"
    assert env["CHOREGOS_OUTPOST_STATE_DIR"]["value"] == STATE
    (mount,) = [m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "policy"]
    assert mount == {"name": "policy", "mountPath": f"{CONFIG}/policy", "readOnly": True}
    # The script's own flag is the daemon's. Read from the command's declaration, not by running it:
    # running the daemon's command line sets the process's logging to a stream that is closed once the
    # run ends, and whatever logs next in this process would write to it.
    import typer
    from choregos_outpost.cli import app

    enrol = typer.main.get_command(app).commands["enrol"]  # type: ignore[attr-defined]
    assert "--token-file" in {option for parameter in enrol.params for option in parameter.opts}


def test_the_outbound_proxy_and_a_private_authority_reach_the_daemon() -> None:
    documents = _documents(
        *MINIMAL,
        'proxy.url="http://proxy.example.com:3128"',
        'ca={"configMap":"house-ca","key":"bundle.pem"}',
        'networkPolicy.controlPlane.cidrs=["203.0.113.7/32"]',
    )
    pod = _pod(documents)
    for container in (pod["initContainers"][0], pod["containers"][0]):
        env = _env(container)
        assert env["CHOREGOS_OUTPOST_PROXY"]["value"] == "http://proxy.example.com:3128"
        assert env["CHOREGOS_OUTPOST_CA_FILE"]["value"] == f"{CONFIG}/ca/bundle.pem"
        assert {"name": "ca", "mountPath": f"{CONFIG}/ca", "readOnly": True} in container["volumeMounts"]
    assert {"name": "ca", "configMap": {"name": "house-ca"}} in pod["volumes"]
    # Neither is set unless asked for: the system's trust store, a direct connection.
    plain = _env(_pod(_documents(*MINIMAL))["containers"][0])
    assert not {"CHOREGOS_OUTPOST_PROXY", "CHOREGOS_OUTPOST_CA_FILE"} & set(plain)
    # An authority may come from a Secret; one of the two.
    secret = _documents(*MINIMAL, 'ca.secret="house-ca"')
    assert [v["secret"]["secretName"] for v in _pod(secret)["volumes"] if v["name"] == "ca"] == ["house-ca"]
    assert "ca: configMap or secret" in _refusal(*MINIMAL, 'ca={"configMap":"a","secret":"b"}')


def test_a_proxy_that_asks_for_a_password_is_a_secret() -> None:
    refusal = _refusal(*MINIMAL, 'proxy.url="http://user:password@proxy.example.com:3128"')
    assert "proxy.existingSecret" in refusal
    documents = _documents(
        *MINIMAL, 'proxy={"existingSecret":"outbound-proxy","key":"url"}',
        'networkPolicy.controlPlane={"cidrs":["10.1.2.3/32"],"port":3128}',
    )  # fmt: skip
    env = _env(_pod(documents)["containers"][0])
    assert env["CHOREGOS_OUTPOST_PROXY"] == {
        "name": "CHOREGOS_OUTPOST_PROXY",
        "valueFrom": {"secretKeyRef": {"name": "outbound-proxy", "key": "url"}},
    }


def test_the_grace_period_stays_below_the_pods() -> None:
    """The daemon lets the calls in progress finish for its grace period, and a write that has begun
    is left to finish: the kubelet must not kill the pod before."""
    documents = _documents(*MINIMAL)
    pod = _pod(documents)
    grace = int(_env(pod["containers"][0])["CHOREGOS_OUTPOST_GRACE_S"]["value"])
    assert grace == 20 < pod["terminationGracePeriodSeconds"] == 30
    longer = _pod(_documents(*MINIMAL, "graceSeconds=60", "terminationGracePeriodSeconds=75"))
    assert _env(longer["containers"][0])["CHOREGOS_OUTPOST_GRACE_S"]["value"] == "60"
    assert longer["terminationGracePeriodSeconds"] == 75
    for termination in (60, 45):
        refusal = _refusal(*MINIMAL, "graceSeconds=60", f"terminationGracePeriodSeconds={termination}")
        assert "terminationGracePeriodSeconds" in refusal and "graceSeconds" in refusal


# ───────────────────────── the network: out to two kinds of places, in from nowhere ─────────────────────────


def _network(documents: list[dict[str, Any]], name: str = "factory-floor-choregos-outpost") -> dict[str, Any]:
    spec: dict[str, Any] = _one(documents, "NetworkPolicy", name)["spec"]
    return spec


def _tcp(port: int) -> list[dict[str, Any]]:
    return [{"protocol": "TCP", "port": port}]


def _namespace(name: str) -> dict[str, Any]:
    return {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": name}}}


def test_egress_goes_to_the_control_plane_and_the_listed_services_only() -> None:
    documents = _documents(
        *MINIMAL,
        TRACKER,
        'policy.endpoints.forge={"url":"https://forge-mcp.forge.svc.cluster.local/mcp","tools":["get_*"]}',
        'networkPolicy.controlPlane.cidrs=["203.0.113.10/32","203.0.113.11/32"]',
    )
    policy = _network(documents)
    assert policy["podSelector"]["matchLabels"] == _daemon(documents)["spec"]["selector"]["matchLabels"]
    assert policy["policyTypes"] == ["Ingress", "Egress"]
    dns, door, forge, tracker = policy["egress"]
    assert dns == {
        "to": [_namespace("kube-system")],
        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
    }
    # The door: its addresses, its port.
    assert door == {
        "to": [{"ipBlock": {"cidr": "203.0.113.10/32"}}, {"ipBlock": {"cidr": "203.0.113.11/32"}}],
        "ports": _tcp(443),
    }
    # Each endpoint: the namespace its Service's name carries, the port of its URL.
    assert tracker == {"to": [_namespace("tools")], "ports": _tcp(8080)}
    assert forge == {"to": [_namespace("forge")], "ports": _tcp(443)}


def test_nothing_may_reach_the_daemon() -> None:
    """An outpost only dials out (ADR 0147 §1): ingress is a policy type, and it has no rule."""
    for sets in (MINIMAL, (*MINIMAL, TRACKER, 'servers=[{"name":"wiki","image":"i@sha256:0","port":8080}]')):
        policy = _network(_documents(*sets))
        assert "Ingress" in policy["policyTypes"] and policy["ingress"] == []


def test_without_the_doors_addresses_its_port_is_opened_to_public_addresses_only() -> None:
    """A NetworkPolicy cannot name a host. Private ranges stay closed: the cluster, the node, the
    cloud's metadata endpoint."""
    _, door = _network(_documents(*MINIMAL))["egress"]
    assert door == {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": PRIVATE}}], "ports": _tcp(443)}
    # The door's own port, when its origin names one.
    _, bench = _network(_documents(MINIMAL[1], 'policy.control_plane="https://outposts.example.com:8443"'))[
        "egress"
    ]
    assert bench["ports"] == _tcp(8443)


def test_behind_a_proxy_the_way_out_is_the_proxy() -> None:
    proxy = 'proxy.url="http://proxy.example.com:3128"'
    refusal = _refusal(*MINIMAL, proxy)
    assert "networkPolicy.controlPlane.cidrs" in refusal and "proxy" in refusal
    _, way = _network(_documents(*MINIMAL, proxy, 'networkPolicy.controlPlane.cidrs=["10.1.2.3/32"]'))[
        "egress"
    ]
    assert way == {"to": [{"ipBlock": {"cidr": "10.1.2.3/32"}}], "ports": _tcp(3128)}
    # A proxy of this cluster is named by its pods; an address is taken as it is.
    in_cluster = 'networkPolicy.controlPlane={"namespace":"egress","podLabels":{"app":"proxy"}}'
    _, way = _network(_documents(*MINIMAL, proxy, in_cluster))["egress"]
    assert way["to"] == [{**_namespace("egress"), "podSelector": {"matchLabels": {"app": "proxy"}}}]
    _, way = _network(_documents(*MINIMAL, 'proxy.url="http://10.9.8.7:3128"'))["egress"]
    assert way == {"to": [{"ipBlock": {"cidr": "10.9.8.7/32"}}], "ports": _tcp(3128)}
    # Its URL in a Secret: the chart cannot read the port.
    hidden = 'proxy.existingSecret="outbound-proxy"'
    assert "networkPolicy.controlPlane.port" in _refusal(
        *MINIMAL, hidden, 'networkPolicy.controlPlane.cidrs=["10.1.2.3/32"]'
    )


def test_an_endpoints_pods_are_named_as_precisely_as_the_operator_says() -> None:
    def endpoint(url: str, network: str | None = None) -> dict[str, Any]:
        extra = f',"network":{network}' if network else ""
        documents = _documents(*MINIMAL, f'policy.endpoints.t={{"url":"{url}","tools":[]{extra}}}')
        *_, rule = _network(documents)["egress"]
        assert "network" not in _policy(documents)["endpoints"]["t"], "the chart's key reached the policy"
        return dict(rule)

    # A bare Service name is a Service of the release's namespace.
    assert endpoint("http://tracker-mcp:8080/mcp") == {"to": [_namespace("outposts")], "ports": _tcp(8080)}
    assert endpoint("http://tracker-mcp.tools/mcp") == {"to": [_namespace("tools")], "ports": _tcp(80)}
    # The pods' labels and the pods' port, when the Service maps another.
    narrowed = endpoint("http://tracker-mcp.tools.svc/mcp", '{"podLabels":{"app":"tracker-mcp"},"port":8080}')
    assert narrowed == {
        "to": [{**_namespace("tools"), "podSelector": {"matchLabels": {"app": "tracker-mcp"}}}],
        "ports": _tcp(8080),
    }
    # An address, and a server outside the cluster by its addresses.
    assert endpoint("http://10.20.30.40:9000/mcp")["to"] == [{"ipBlock": {"cidr": "10.20.30.40/32"}}]
    outside = endpoint("https://tracker.internal.example.com/mcp", '{"cidrs":["10.50.0.0/24"]}')
    assert outside == {"to": [{"ipBlock": {"cidr": "10.50.0.0/24"}}], "ports": _tcp(443)}


def test_an_endpoint_a_network_policy_cannot_name_fails_the_render() -> None:
    endpoint = 'policy.endpoints.t={"url":"https://tracker.internal.example.com/mcp","tools":[]}'
    refusal = _refusal(*MINIMAL, endpoint)
    assert "policy.endpoints.t.network.cidrs" in refusal and "tracker.internal.example.com" in refusal
    # Without the policy the chart has nothing to name, and says nothing.
    documents = _documents(*MINIMAL, endpoint, "networkPolicy.enabled=false")
    assert not [d for d in documents if d["kind"] == "NetworkPolicy"]


def test_further_egress_is_the_operators_to_write() -> None:
    rule = '{"to":[{"ipBlock":{"cidr":"198.51.100.0/24"}}],"ports":[{"protocol":"TCP","port":514}]}'
    *_, last = _network(_documents(*MINIMAL, f"networkPolicy.extraEgress=[{rule}]"))["egress"]
    assert last == {"to": [{"ipBlock": {"cidr": "198.51.100.0/24"}}], "ports": _tcp(514)}
    node_local = _network(_documents(*MINIMAL, 'networkPolicy.dns.cidrs=["169.254.20.10/32"]'))["egress"][0]
    assert {"ipBlock": {"cidr": "169.254.20.10/32"}} in node_local["to"]


# ───────────────────────── servers the operator runs beside the daemon ─────────────────────────

WIKI = (
    'servers=[{"name":"wiki","image":"registry.example.com/tools/wiki-mcp@sha256:%s","port":8080,'
    '"env":{"LOG_LEVEL":"info"},"envFromSecrets":["wiki-mcp-env"]}]' % ("1" * 64)
)


def test_no_server_is_run_unless_the_operator_lists_one() -> None:
    kinds = sorted(d["kind"] for d in _documents(*MINIMAL))
    assert kinds == ["ConfigMap", "NetworkPolicy", "ServiceAccount", "StatefulSet"]


def test_a_listed_server_is_locked_down_and_reachable_from_the_daemon_only() -> None:
    documents = _documents(*MINIMAL, WIKI)
    name = "factory-floor-choregos-outpost-wiki"
    server = _one(documents, "Deployment", name)
    pod = server["spec"]["template"]["spec"]
    _locked_down_as_the_model(pod, pod["containers"])
    (container,) = pod["containers"]
    assert container["image"].startswith("registry.example.com/tools/wiki-mcp@sha256:")
    # Its environment: plain values, and Secrets the operator created, by reference.
    assert container["env"] == [{"name": "LOG_LEVEL", "value": "info"}]
    assert container["envFrom"] == [{"secretRef": {"name": "wiki-mcp-env"}}]
    assert not [d for d in documents if d["kind"] == "Secret"], "the chart writes no secret"
    service = _one(documents, "Service", name)
    assert service["spec"]["selector"] == server["spec"]["selector"]["matchLabels"]
    assert service["spec"]["ports"][0]["port"] == 8080
    # In: the daemon's pods and nobody else. Out: nothing, unless the operator opens it.
    policy = _network(documents, name)
    assert policy["podSelector"]["matchLabels"] == server["spec"]["selector"]["matchLabels"]
    assert policy["policyTypes"] == ["Ingress", "Egress"] and policy["egress"] == []
    daemon_labels = _daemon(documents)["spec"]["selector"]["matchLabels"]
    assert policy["ingress"] == [
        {"from": [{"podSelector": {"matchLabels": daemon_labels}}], "ports": _tcp(8080)}
    ]
    # The daemon may reach it, by its pods; and the daemon's own selector does not select it.
    *_, to_the_server = _network(documents)["egress"]
    assert to_the_server == {
        "to": [{"podSelector": {"matchLabels": server["spec"]["selector"]["matchLabels"]}}],
        "ports": _tcp(8080),
    }
    assert not daemon_labels.items() <= server["spec"]["template"]["metadata"]["labels"].items()


def test_an_endpoint_at_a_listed_server_opens_that_server_and_not_its_namespace() -> None:
    endpoint = (
        'policy.endpoints.wiki={"url":"http://factory-floor-choregos-outpost-wiki:8080/mcp","tools":[]}'
    )
    egress = _network(_documents(*MINIMAL, WIKI, endpoint))["egress"]
    assert len(egress) == 3, "DNS, the door, the server's pods: no rule for the whole namespace"
    assert not [rule for rule in egress if rule["to"] == [_namespace("outposts")]]


def test_a_server_may_be_given_a_way_out_and_must_be_named() -> None:
    rule = '{"to":[{"ipBlock":{"cidr":"10.60.0.5/32"}}],"ports":[{"protocol":"TCP","port":5432}]}'
    image = f"registry.example.com/db-mcp@sha256:{'2' * 64}"
    opened = f'servers=[{{"name":"db","image":"{image}","port":9000,"egress":[{rule}]}}]'
    policy = _network(_documents(*MINIMAL, opened), "factory-floor-choregos-outpost-db")
    assert policy["egress"] == [{"to": [{"ipBlock": {"cidr": "10.60.0.5/32"}}], "ports": _tcp(5432)}]
    assert "servers[0].name" in _refusal(*MINIMAL, 'servers=[{"image":"i","port":1}]')
    assert "servers[0].image" in _refusal(*MINIMAL, 'servers=[{"name":"a","port":1}]')
    assert "servers[0].port" in _refusal(*MINIMAL, 'servers=[{"name":"a","image":"i"}]')
    twice = 'servers=[{"name":"a","image":"i","port":1},{"name":"a","image":"i","port":2}]'
    assert "names two servers" in _refusal(*MINIMAL, twice)


# ───────────────────────── linted, tested and published beside the platform's ─────────────────────────


def test_the_example_values_render_a_complete_outpost() -> None:
    """What `make charts-lint` and the CI lint and validate: an outpost with an endpoint, a
    credential, a server and the door's addresses."""
    kinds = sorted(d["kind"] for d in _documents(values=EXAMPLE))
    assert kinds == [
        "ConfigMap",
        "Deployment",
        "NetworkPolicy",
        "NetworkPolicy",
        "Service",
        "ServiceAccount",
        "StatefulSet",
    ]


def test_the_chart_is_linted_and_published_beside_the_platforms() -> None:
    lint = "helm lint charts/choregos-outpost -f charts/choregos-outpost/values/example.yaml"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert lint in makefile.split("charts-lint:", 1)[1].split(".PHONY", 1)[0]
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = "\n".join(str(step.get("run", "")) for step in ci["jobs"]["charts"]["steps"])
    assert lint in steps
    assert "helm template outpost charts/choregos-outpost" in steps and "/tmp/outpost.yaml" in steps
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    published = "\n".join(str(step.get("run", "")) for step in release["jobs"]["chart"]["steps"])
    for chart in ("choregos", "choregos-outpost"):
        assert f'helm package charts/{chart} --version "$version" --app-version "$version"' in published
        assert f'helm push "{chart}-$version.tgz" oci://ghcr.io/vargafoundation/charts' in published
    # After the images: a published chart never points at an image that was not pushed.
    assert release["jobs"]["chart"]["needs"] == "manifest"
    assert "outpost" in release["jobs"]["manifest"]["strategy"]["matrix"]["image"]
