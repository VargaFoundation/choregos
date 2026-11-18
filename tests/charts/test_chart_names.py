# SPDX-License-Identifier: Apache-2.0
"""One image and one chart, two names (ADR 0176 R3, S75-03).

A release publishes every image as `<registry>/tranibor-<component>` and, for the deprecation window,
as `<registry>/choregos-<component>`: one manifest list, one digest, each name signed by the release
workflow. It publishes each chart as `tranibor` and `choregos` (`tranibor-outpost`, `choregos-outpost`),
packaged from one directory. The names come from the publishing identity (`image_prefix`,
`image_alias_prefix`, ADR 0156). What a deployment must be able to rely on:

* the two names of a chart render the same objects, so moving from one to the other is a change of
  address and nothing else (the objects' names do not follow the chart's);
* the release packages and pushes each chart under both names, and builds, tags and signs each image
  under both;
* the reference admission policy admits the images under both names, each with the release's
  signature, and does not take the plugins' images (`<registry>/tranibor-agents/...`) for the core's.

The chart's own defaults still name the images `choregos-*`: deployments switch, one environment at a
time, in step (c) of R3, not here.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import pathlib
import shutil
import subprocess
import tarfile
from typing import Any

import pytest
import yaml
from tranibor_core.publishing import CHARTS, IMAGE_COMPONENTS, PUBLISHING

ROOT = pathlib.Path(__file__).resolve().parents[2]
# The tool the release runs (`tests/tools/conftest.py` puts the repository on the path for its own
# directory only).
_SPEC = importlib.util.spec_from_file_location("package_charts", ROOT / "tools/package_charts.py")
assert _SPEC is not None and _SPEC.loader is not None
package_charts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(package_charts)
RELEASE = ROOT / ".github/workflows/release.yml"
POLICY = ROOT / "infra/policies/images.yaml"
#: The values each chart is rendered with: the outpost's chart refuses to render without its own, and
#: its example names the objects itself (`fullnameOverride`), which would hide the default naming.
VALUES = {
    "": [],
    "-outpost": [
        "-f",
        str(ROOT / "charts/choregos-outpost/values/example.yaml"),
        "--set",
        "fullnameOverride=",
    ],
}

helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(chart: pathlib.Path, *arguments: str) -> list[dict[str, Any]]:
    command = ["helm", "template", "platform", str(chart), *arguments]
    output = subprocess.run(command, capture_output=True, text=True, check=True, timeout=180)
    # The `# Source: <chart>/templates/...` comments name the chart; the objects must not.
    return [doc for doc in yaml.safe_load_all(output.stdout) if doc]


def _pod_images(docs: list[dict[str, Any]]) -> set[str]:
    images: set[str] = set()
    for doc in docs:
        spec = doc.get("spec") or {}
        pod = (spec.get("jobTemplate", {}).get("spec") or spec).get("template", {}).get("spec")
        if not pod:
            continue
        for container in pod.get("initContainers", []) + pod.get("containers", []):
            images.add(container["image"])
    return images


# ───────────────────────────── the charts ─────────────────────────────


@helm
@pytest.mark.parametrize("suffix", sorted(CHARTS))
def test_both_names_of_a_chart_render_the_same_objects(suffix: str, tmp_path: pathlib.Path) -> None:
    current, alias = PUBLISHING.chart_names(suffix)
    renders = {
        name: _render(package_charts.renamed(ROOT / CHARTS[suffix], name, tmp_path), *VALUES[suffix])
        for name in (current, alias)
    }
    assert renders[current], f"{current} renders nothing"
    assert renders[current] == renders[alias]


@helm
def test_the_release_packages_each_chart_under_both_names(tmp_path: pathlib.Path) -> None:
    archives = package_charts.package("9.9.9", tmp_path)
    names = [name for suffix in CHARTS for name in PUBLISHING.chart_names(suffix)]
    assert archives == [tmp_path / f"{name}-9.9.9.tgz" for name in names]
    assert {"tranibor", "choregos", "tranibor-outpost", "choregos-outpost"} == set(names)
    for name, archive in zip(names, archives, strict=True):
        with tarfile.open(archive) as bundle:
            member = bundle.extractfile(f"{name}/Chart.yaml")
            assert member is not None
            chart = yaml.safe_load(member.read())
        assert (chart["name"], chart["version"], chart["appVersion"]) == (name, "9.9.9", "9.9.9")


# ───────────────────────────── the images ─────────────────────────────


def test_the_release_builds_tags_and_signs_each_image_under_both_names() -> None:
    release = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    images, manifest = release["jobs"]["images"], release["jobs"]["manifest"]
    # Every image the identity knows is built and signed, and no other (quant is a command, #1847).
    assert images["strategy"]["matrix"]["image"] == list(IMAGE_COMPONENTS)
    assert manifest["strategy"]["matrix"]["image"] == list(IMAGE_COMPONENTS)
    current, alias = (
        PUBLISHING.image(f"{prefix}-${{{{ matrix.image }}}}") for prefix in PUBLISHING.image_prefixes
    )
    assert images["env"] == {"IMAGE": current}
    assert manifest["env"] == {"IMAGE": current, "ALIAS": alias}
    meta = next(step for step in manifest["steps"] if step.get("id") == "meta")
    assert meta["with"]["images"].splitlines() == ["${{ env.IMAGE }}", "${{ env.ALIAS }}"]
    runs = "\n".join(str(step.get("run", "")) for step in manifest["steps"])
    # One manifest list for every tag of both names, then one signature per name, of the same digest.
    assert '$(printf "${IMAGE}@sha256:%s " *)' in runs
    assert 'test "$digest" = "$alias"' in runs
    assert 'cosign sign --yes "${IMAGE}@${digest}"' in runs
    assert 'cosign sign --yes "${ALIAS}@${digest}"' in runs


def _verify_entries() -> list[dict[str, Any]]:
    (rule,) = yaml.safe_load(POLICY.read_text(encoding="utf-8"))["spec"]["rules"]
    entries: list[dict[str, Any]] = rule["verifyImages"]
    return entries


def _admitting(image: str) -> list[set[str]]:
    """The signers each `verifyImages` entry that takes `image` demands (Kyverno's `*` crosses `/`)."""
    return [
        {entry["keyless"]["subject"] for attestor in item["attestors"] for entry in attestor["entries"]}
        for item in _verify_entries()
        if any(fnmatch.fnmatchcase(image, pattern) for pattern in item["imageReferences"])
    ]


def test_the_policy_admits_each_image_under_both_names_with_the_release_signature() -> None:
    signer = f"{PUBLISHING.source_repository}/.github/workflows/*"
    for name in (n for component in IMAGE_COMPONENTS for n in PUBLISHING.image_names(component)):
        for reference in (f"{name}:9.9.9", f"{name}@sha256:{'0' * 64}"):
            assert _admitting(reference) == [{signer}], reference
    plugin = PUBLISHING.image(f"{PUBLISHING.image_prefix}-agents/hello-mcp@sha256:{'0' * 64}")
    assert _admitting(plugin) == [], "a plugin server would need the core's signature"


@helm
def test_the_chart_renders_the_new_names_and_the_policy_admits_them() -> None:
    renamed = {
        "choregos-api.image.repository": "api",
        "choregos-web.image.repository": "web",
        "choregos-orchestrator.image.repository": "orchestrator",
        "choregos-tools.image.repository": "tools",
        "choregos-orchestrator.runner.image": "runner",
    }
    for prefix in PUBLISHING.image_prefixes:
        arguments = [f"--set={key}={prefix}-{component}" for key, component in renamed.items()]
        rendered = _pod_images(_render(ROOT / CHARTS[""], *arguments))
        named = {image for image in rendered if image.startswith(PUBLISHING.image(f"{prefix}-"))}
        for component in ("api", "web", "orchestrator"):
            assert any(image.startswith(PUBLISHING.image(f"{prefix}-{component}:")) for image in named), named
        for image in named:
            assert _admitting(image) == [{f"{PUBLISHING.source_repository}/.github/workflows/*"}], image
