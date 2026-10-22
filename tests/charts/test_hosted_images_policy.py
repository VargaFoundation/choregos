"""A hosted agent's image, in the chart (S41-14): the signer reaches the API, and the Kyverno policy
admits an image under `hostedRegistries` only pinned and, under enforce, signed, init containers
(native sidecars, #770) included.

The rendering tests need helm. The Kyverno tests run the policy the chart renders through the
`kyverno` CLI: the digest rule offline, the signature rule against a registry with images signed
here (marker `cosign_real`, the `hosted-cosign` CI job). Offline, Kyverno cannot read a Secret: that
test hands the policy the same public key inline, in place of the Secret reference.
"""

from __future__ import annotations

import io
import os
import pathlib
import re
import shutil
import subprocess
import tarfile
import uuid
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
DIGEST = "sha256:" + "a" * 64
REGISTRY = os.environ.get("CHOREGOS_COSIGN_REAL_REGISTRY", "")
POLICY = "templates/hosted-images-policy.yaml"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
needs_kyverno = pytest.mark.skipif(shutil.which("kyverno") is None, reason="no kyverno CLI on the path")


def _render(*overrides: str, show: str | None = None) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART), "--namespace", "tenant-a"]
    for override in overrides:
        # A regular expression keeps its backslashes: `--set` would read them as escapes.
        literal = override.split("=", 1)[0].endswith(("Identity", "Issuer"))
        command += ["--set-literal" if literal else "--set", override]
    if show:
        command += ["--show-only", show]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _documents(*overrides: str) -> list[dict[str, Any]]:
    rendered = _render(*overrides)
    assert rendered.returncode == 0, rendered.stderr
    return [d for d in yaml.safe_load_all(rendered.stdout) if d]


def _api(*overrides: str) -> dict[str, Any]:
    (api,) = (
        d
        for d in _documents(*overrides)
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "choregos-api"
    )
    pod: dict[str, Any] = api["spec"]["template"]["spec"]
    return pod


def _env(pod: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e["value"] for e in pod["containers"][0].get("env", []) if "value" in e}


def _policy(*overrides: str) -> dict[str, Any]:
    rendered = _render(*overrides, show=POLICY)
    assert rendered.returncode == 0, rendered.stderr
    policy: dict[str, Any] = yaml.safe_load(rendered.stdout)
    return policy


ENFORCED = (
    "global.images.hostedRegistries={registry.internal/acme,ghcr.io/acme/}",
    "global.images.hostedCosign=enforce",
    "global.images.hostedCosignKeySecret=acme-cosign",
    "global.images.hostedCosignPolicy=true",
)


def test_by_default_no_signer_no_policy_and_the_log_is_checked() -> None:
    pod = _api()
    env = _env(pod)
    assert env["CHOREGOS_HOSTED_COSIGN_TLOG"] == "true"
    assert not {"CHOREGOS_HOSTED_COSIGN_KEY", "CHOREGOS_HOSTED_COSIGN_IDENTITY", "TUF_ROOT"} & set(env)
    assert all(v["name"] != "cosign" for v in pod["volumes"])
    assert not [d for d in _documents() if d["kind"] == "ClusterPolicy"]


def test_the_key_secret_is_mounted_on_the_api_and_named_to_it() -> None:
    pod = _api("global.images.hostedCosign=enforce", "global.images.hostedCosignKeySecret=acme-cosign")
    env = _env(pod)
    assert env["CHOREGOS_HOSTED_COSIGN_KEY"] == "/etc/choregos/cosign/cosign.pub"
    assert env["TUF_ROOT"].startswith("/tmp/"), "cosign's cache, on the one writable path"
    (volume,) = (v for v in pod["volumes"] if v["name"] == "cosign")
    assert volume["secret"] == {
        "secretName": "acme-cosign",
        "items": [{"key": "cosign.pub", "path": "cosign.pub"}],
    }
    mounts = {m["name"]: m for m in pod["containers"][0]["volumeMounts"]}
    assert mounts["cosign"]["mountPath"] == "/etc/choregos/cosign" and mounts["cosign"]["readOnly"]


def test_keyless_the_identity_and_issuer_reach_the_api_and_tlog_can_be_off() -> None:
    env = _env(
        _api(
            "global.images.hostedCosignIdentity=^https://github\\.com/acme/.+$",
            "global.images.hostedCosignIssuer=^https://token\\.actions\\.githubusercontent\\.com$",
            "global.images.hostedCosignTlog=false",
        )
    )
    assert env["CHOREGOS_HOSTED_COSIGN_IDENTITY"] == "^https://github\\.com/acme/.+$"
    assert env["CHOREGOS_HOSTED_COSIGN_ISSUER"] == "^https://token\\.actions\\.githubusercontent\\.com$"
    assert env["CHOREGOS_HOSTED_COSIGN_TLOG"] == "false"
    assert "CHOREGOS_HOSTED_COSIGN_KEY" not in env


def test_a_key_and_an_identity_together_do_not_render() -> None:
    rendered = _render("global.images.hostedCosignKeySecret=k", "global.images.hostedCosignIdentity=x")
    assert rendered.returncode != 0 and "not both" in rendered.stderr


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (("global.images.hostedCosignPolicy=true",), "needs global.images.hostedRegistries"),
        (
            ("global.images.hostedCosignPolicy=true", "global.images.hostedRegistries={ghcr.io/acme}",
             "global.images.hostedCosign=enforce"),
            "needs a signer",
        ),
    ],
)  # fmt: skip
def test_a_policy_that_would_cover_nothing_does_not_render(overrides: tuple[str, ...], reason: str) -> None:
    rendered = _render(*overrides)
    assert rendered.returncode != 0 and reason in rendered.stderr


def test_the_policy_pins_every_container_list_and_verifies_the_signer() -> None:
    policy = _policy(*ENFORCED)
    assert policy["kind"] == "ClusterPolicy" and policy["spec"]["validationFailureAction"] == "Enforce"
    rules = {rule["name"]: rule for rule in policy["spec"]["rules"]}
    for rule in rules.values():
        (match,) = rule["match"]["any"]
        assert set(match["resources"]["namespaces"]) == {"tenant-a", "choregos-*", "proj-*"}
    (each,) = rules["hosted-image-pinned"]["validate"]["foreach"]
    assert each["list"] == "request.object.spec.[ephemeralContainers, initContainers, containers][]"
    prefixes = each["deny"]["conditions"]["all"][0]["value"]
    assert prefixes == ["registry.internal/acme/*", "ghcr.io/acme/*"]
    (verify,) = rules["hosted-image-signed"]["verifyImages"]
    assert verify["imageReferences"] == prefixes
    assert verify["verifyDigest"] is True and verify["required"] is True
    (entry,) = verify["attestors"][0]["entries"]
    assert entry == {"keys": {"secret": {"name": "acme-cosign", "namespace": "tenant-a"}}}


def test_under_warn_the_policy_pins_and_keyless_verifies_the_identity() -> None:
    warned = _policy(
        "global.images.hostedRegistries={ghcr.io/acme}",
        "global.images.hostedCosign=warn",
        "global.images.hostedCosignPolicy=true",
    )
    assert [rule["name"] for rule in warned["spec"]["rules"]] == ["hosted-image-pinned"]
    keyless = _policy(
        "global.images.hostedRegistries={ghcr.io/acme}",
        "global.images.hostedCosign=enforce",
        "global.images.hostedCosignIdentity=^https://github\\.com/acme/.+$",
        "global.images.hostedCosignIssuer=^https://token\\.actions\\.githubusercontent\\.com$",
        "global.images.hostedCosignTlog=false",
        "global.images.hostedCosignPolicy=true",
    )
    (entry,) = keyless["spec"]["rules"][1]["verifyImages"][0]["attestors"][0]["entries"]
    assert entry["keyless"] == {
        "subjectRegExp": "^https://github\\.com/acme/.+$",
        "issuerRegExp": "^https://token\\.actions\\.githubusercontent\\.com$",
    }


def _pod(name: str, namespace: str, agent: str, *, sidecar: bool) -> dict[str, Any]:
    runner = {"name": "runner", "image": f"ghcr.io/vargafoundation/choregos-runner@{DIGEST}"}
    spec: dict[str, Any] = {"containers": [runner]}
    if sidecar:
        spec["initContainers"] = [{"name": "agent", "image": agent, "restartPolicy": "Always"}]
    else:
        spec["containers"].append({"name": "agent", "image": agent})
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


def _kyverno(tmp: pathlib.Path, policy: dict[str, Any], pod: dict[str, Any]) -> bool:
    """Whether Kyverno admits `pod` under `policy`, as `kyverno apply` judges it."""
    (tmp / "policy.yaml").write_text(yaml.safe_dump(policy))
    (tmp / "pod.yaml").write_text(yaml.safe_dump(pod))
    done = subprocess.run(
        ["kyverno", "apply", str(tmp / "policy.yaml"), "--resource", str(tmp / "pod.yaml")],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    found = re.search(r"pass: (\d+), fail: (\d+), warn: \d+, error: (\d+)", done.stdout)
    assert found is not None, done.stdout + done.stderr
    assert found.group(3) == "0", done.stdout
    return found.group(2) == "0"


@needs_kyverno
@pytest.mark.parametrize(
    ("pod", "admitted"),
    [
        (_pod("tagged-sidecar", "proj-acme", "registry.internal/acme/triager:1.2", sidecar=True), False),
        (_pod("tagged-agent", "tenant-a", "ghcr.io/acme/deep/triager:1.2", sidecar=False), False),
        (_pod("pinned-sidecar", "proj-acme", f"registry.internal/acme/triager@{DIGEST}", sidecar=True), True),
        (_pod("other-registry", "proj-acme", "ghcr.io/acme-evil/triager:1", sidecar=True), True),
    ],
    ids=lambda value: value["metadata"]["name"] if isinstance(value, dict) else str(value),
)
def test_kyverno_refuses_a_hosted_image_by_tag_in_any_container_list(
    tmp_path: pathlib.Path, pod: dict[str, Any], admitted: bool
) -> None:
    policy = _policy(*ENFORCED)
    policy["spec"]["rules"] = [r for r in policy["spec"]["rules"] if r["name"] == "hosted-image-pinned"]
    assert _kyverno(tmp_path, policy, pod) is admitted


def _run(*argv: str, cwd: pathlib.Path | None = None) -> str:
    env = {**os.environ, "COSIGN_PASSWORD": ""}
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=120)
    assert done.returncode == 0, f"{argv}: {done.stderr}"
    return done.stdout.strip()


@pytest.mark.cosign_real
@needs_kyverno
@pytest.mark.skipif(
    not REGISTRY or shutil.which("cosign") is None or shutil.which("crane") is None,
    reason="no registry (CHOREGOS_COSIGN_REAL_REGISTRY), or no cosign or crane on the path",
)
def test_kyverno_admits_a_hosted_image_only_signed_by_the_key(tmp_path: pathlib.Path) -> None:
    _run("cosign", "generate-key-pair", cwd=tmp_path)
    name, pinned = f"{REGISTRY}/s41-14/policy", {}
    for tag in ("signed", "unsigned"):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            content = f"{tag} {uuid.uuid4()}".encode()
            info = tarfile.TarInfo("hello.txt")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        (tmp_path / f"{tag}.tar").write_bytes(buffer.getvalue())
        _run("crane", "append", "--insecure", "-f", str(tmp_path / f"{tag}.tar"), "-t", f"{name}:{tag}")
        pinned[tag] = f"{name}@{_run('crane', 'digest', '--insecure', f'{name}:{tag}')}"
    _run(
        "cosign", "sign", "--yes", "--key", str(tmp_path / "cosign.key"), "--tlog-upload=false",
        "--use-signing-config=false", "--new-bundle-format=false", "--allow-http-registry",
        "--allow-insecure-registry", pinned["signed"],
    )  # fmt: skip
    policy = _policy(
        f"global.images.hostedRegistries={{{REGISTRY}/s41-14}}",
        "global.images.hostedCosign=enforce",
        "global.images.hostedCosignKeySecret=acme-cosign",
        "global.images.hostedCosignTlog=false",
        "global.images.hostedCosignPolicy=true",
    )
    (entry,) = policy["spec"]["rules"][1]["verifyImages"][0]["attestors"][0]["entries"]
    assert entry["keys"].pop("secret") == {"name": "acme-cosign", "namespace": "tenant-a"}
    entry["keys"]["publicKeys"] = (tmp_path / "cosign.pub").read_text()
    assert _kyverno(tmp_path, policy, _pod("signed-sidecar", "proj-acme", pinned["signed"], sidecar=True))
    assert not _kyverno(
        tmp_path, policy, _pod("unsigned-sidecar", "proj-acme", pinned["unsigned"], sidecar=True)
    )
    assert not _kyverno(
        tmp_path, policy, _pod("unsigned-agent", "proj-acme", pinned["unsigned"], sidecar=False)
    )
