"""S41-25 - the chart's hosted-images Kyverno policy, in a real Kyverno on a real cluster.

`tests/charts/test_hosted_images_policy.py` judges the rendered policy with the `kyverno` CLI. This
test installs nothing itself: it needs a cluster with Kyverno running (the admission controller
started with `--allowInsecureRegistry`, the registry here speaks HTTP) and a registry that the host
reaches (`CHOREGOS_ADMISSION_REGISTRY_PUSH`, e.g. `localhost:5025`) and the cluster reaches under
another name (`CHOREGOS_ADMISSION_REGISTRY_CLUSTER`, e.g. `s41-25-reg:5000`). Both name the same
registry; without them, or without `cosign`, `crane`, `helm` or `kubectl`, every test is skipped.

It renders the chart's policy (`hostedCosign=enforce`, a key), applies it, and creates Pods:

- an image nobody signed: refused;
- an image signed in cosign's legacy `.sig` format: admitted;
- an image signed in cosign 3's bundle format (an OCI referrer): REFUSED by Kyverno 1.19.1 ("no
  signatures found"), pinned here as a strict `xfail` so the day Kyverno reads bundles the suite says so;
- a signature of one image copied onto another's tag: refused;
- a signed image named by a tag, without a digest: refused by the digest rule;
- a signed image from a registry the policy does not cover: not asked about its signature.

The Pods are never scheduled to run: admission is the verdict, the image is not pulled.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import subprocess
import tarfile
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import yaml

from .conftest import context

PUSH = os.environ.get("CHOREGOS_ADMISSION_REGISTRY_PUSH", "")
INCLUSTER = os.environ.get("CHOREGOS_ADMISSION_REGISTRY_CLUSTER", "")
CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
RELEASE_NAMESPACE = "choregos-admission"
POD_NAMESPACE = "proj-admission"
SECRET = "acme-cosign"

pytestmark = [
    pytest.mark.cluster,
    pytest.mark.skipif(
        not PUSH
        or not INCLUSTER
        or any(shutil.which(tool) is None for tool in ("cosign", "crane", "helm", "kubectl")),
        reason="no registry (CHOREGOS_ADMISSION_REGISTRY_PUSH and _CLUSTER), or a tool missing",
    ),
]


def _run(
    *argv: str, cwd: pathlib.Path | None = None, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "COSIGN_PASSWORD": "", **(env or {})}
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=180)
    assert not check or done.returncode == 0, f"{argv}: {done.stderr}"
    return done


def _kubectl(
    *args: str, input_text: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    command = ["kubectl", "--context", context(), *args]
    done = subprocess.run(command, input=input_text, capture_output=True, text=True, check=False, timeout=180)
    assert not check or done.returncode == 0, f"{command}: {done.stderr}"
    return done


@dataclass(frozen=True)
class Images:
    signed_legacy: str
    signed_bundle: str
    unsigned: str
    altered: str
    tagged: str
    uncovered: str


def _image(tmp: pathlib.Path, repository: str, tag: str) -> str:
    """A one-layer image pushed as `<push registry>/<repository>:<tag>`; its digest."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        content = f"{tag} {uuid.uuid4()}".encode()
        info = tarfile.TarInfo("hello.txt")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    layer = tmp / f"{tag}.tar"
    layer.write_bytes(buffer.getvalue())
    name = f"{PUSH}/{repository}"
    _run("crane", "append", "--insecure", "-f", str(layer), "-t", f"{name}:{tag}")
    return _run("crane", "digest", "--insecure", f"{name}:{tag}").stdout.strip()


@pytest.fixture(scope="module")
def images(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Images]:
    tmp = tmp_path_factory.mktemp("admission")
    _run("cosign", "generate-key-pair", cwd=tmp)
    digests = {tag: _image(tmp, "hosted/agent", tag) for tag in ("legacy", "bundle", "unsigned", "altered")}
    digests["uncovered"] = _image(tmp, "elsewhere/agent", "uncovered")
    sign = (
        "cosign", "sign", "--yes", "--key", str(tmp / "cosign.key"), "--tlog-upload=false",
        "--use-signing-config=false", "--allow-http-registry", "--allow-insecure-registry",
    )  # fmt: skip
    _run(*sign, "--new-bundle-format=false", f"{PUSH}/hosted/agent@{digests['legacy']}")
    _run(*sign, f"{PUSH}/hosted/agent@{digests['bundle']}")
    _run(  # the legacy signature of `legacy`, on the tag cosign reads for `altered`
        "crane", "copy", "--insecure",
        f"{PUSH}/hosted/agent:{digests['legacy'].replace(':', '-')}.sig",
        f"{PUSH}/hosted/agent:{digests['altered'].replace(':', '-')}.sig",
    )  # fmt: skip

    def inside(repository: str, digest: str) -> str:
        return f"{INCLUSTER}/{repository}@{digest}"

    rendered = _run(
        "helm", "template", "choregos", str(CHART), "--namespace", RELEASE_NAMESPACE,
        "--set", f"global.images.hostedRegistries={{{INCLUSTER}/hosted}}",
        "--set", "global.images.hostedCosign=enforce",
        "--set", f"global.images.hostedCosignKeySecret={SECRET}",
        "--set", "global.images.hostedCosignTlog=false",
        "--set", "global.images.hostedCosignPolicy=true",
        "--show-only", "templates/hosted-images-policy.yaml",
        "--show-only", "templates/hosted-images-policy-rbac.yaml",
        "--api-versions", "extensions.agents.x-k8s.io/v1beta1/SandboxTemplate",
        # Rendered offline, as the chart's own tests do: a `lookup` of this cluster's CRDs is not the point.
        env={"KUBECONFIG": os.devnull},
    ).stdout  # fmt: skip
    for namespace in (RELEASE_NAMESPACE, POD_NAMESPACE):  # a run that was cut short leaves them
        _kubectl("delete", "namespace", namespace, "--ignore-not-found", "--timeout=120s")
        _kubectl("create", "namespace", namespace)
    _kubectl(
        "-n", RELEASE_NAMESPACE, "create", "secret", "generic", SECRET,
        f"--from-file=cosign.pub={tmp / 'cosign.pub'}",
    )  # fmt: skip
    _kubectl("-n", RELEASE_NAMESPACE, "apply", "-f", "-", input_text=rendered)
    _wait_for_policy()
    try:
        yield Images(
            signed_legacy=inside("hosted/agent", digests["legacy"]),
            signed_bundle=inside("hosted/agent", digests["bundle"]),
            unsigned=inside("hosted/agent", digests["unsigned"]),
            altered=inside("hosted/agent", digests["altered"]),
            tagged=f"{INCLUSTER}/hosted/agent:legacy",  # signed, but named by a tag
            uncovered=inside("elsewhere/agent", digests["uncovered"]),
        )
    finally:
        _kubectl("delete", "clusterpolicy", "choregos-hosted-images", check=False)
        for namespace in (RELEASE_NAMESPACE, POD_NAMESPACE):
            _kubectl("delete", "namespace", namespace, "--wait=false", check=False)


def _wait_for_policy() -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        ready = _kubectl(
            "get",
            "clusterpolicy",
            "choregos-hosted-images",
            "-o",
            "jsonpath={.status.conditions[?(@.type=='Ready')].status}",
            check=False,
        ).stdout
        if ready == "True":
            return
        time.sleep(3)
    raise AssertionError("Kyverno never reported the policy ready")


def _create(name: str, image: str, *, sidecar: bool = False) -> subprocess.CompletedProcess[str]:
    spec: dict[str, object] = {"containers": [{"name": "agent", "image": image}]}
    if sidecar:
        spec = {
            "containers": [{"name": "runner", "image": "registry.invalid/runner:1"}],
            "initContainers": [{"name": "agent", "image": image, "restartPolicy": "Always"}],
        }
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": POD_NAMESPACE},
        "spec": spec,
    }
    return _kubectl("create", "-f", "-", input_text=yaml.safe_dump(json.loads(json.dumps(pod))), check=False)


def test_an_unsigned_image_is_refused(images: Images) -> None:
    done = _create("unsigned", images.unsigned)
    assert done.returncode != 0 and "no signatures found" in done.stderr, done.stderr


def test_an_unsigned_native_sidecar_is_refused(images: Images) -> None:
    done = _create("unsigned-sidecar", images.unsigned, sidecar=True)
    assert done.returncode != 0 and "no signatures found" in done.stderr, done.stderr


def test_an_image_signed_in_the_legacy_format_is_admitted(images: Images) -> None:
    done = _create("legacy", images.signed_legacy)
    assert done.returncode == 0, done.stderr


@pytest.mark.xfail(
    strict=True,
    reason="Kyverno 1.19.1's `keys` attestor finds no signature in a cosign 3 bundle (finding S41-25)",
)
def test_an_image_signed_in_the_bundle_format_is_admitted(images: Images) -> None:
    done = _create("bundle", images.signed_bundle)
    assert done.returncode == 0, done.stderr


def test_a_signature_copied_onto_other_bytes_is_refused(images: Images) -> None:
    done = _create("altered", images.altered)
    assert done.returncode != 0, done.stderr


def test_a_tag_without_a_digest_is_refused(images: Images) -> None:
    done = _create("tagged", images.tagged)
    assert done.returncode != 0 and "pinned by `@sha256:`" in done.stderr, done.stderr


def test_an_image_outside_the_covered_registry_is_not_asked_for_a_signature(images: Images) -> None:
    done = _create("uncovered", images.uncovered)
    assert done.returncode == 0, done.stderr
