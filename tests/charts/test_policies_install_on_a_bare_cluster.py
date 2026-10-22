"""The default policy set installs on a cluster that has only Kyverno and the platform's operators.

A server-side dry run on the dev cluster (Kyverno 1.12.5) refused two of the five policies, so
none of them were ever installed there: a keyless attestor without a Rekor URL ("Either Rekor URL
or roots are required"), and a rule matching `PipelineRun`, a Tekton kind on a cluster without
Tekton ("unable to convert GVK to GVR"). Kyverno refuses the whole ClusterPolicy for one such
rule, so an optional executor's kind takes the platform's pod rules down with it.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICIES = ROOT / "infra" / "policies"

# Built-in kinds, plus the agent-sandbox operator's: it is the default executor (ADR 0072) and the
# chart refuses to render without it, so its CRDs are on every cluster the platform runs on.
KNOWN_KINDS = {
    "Pod",
    "Job",
    "Namespace",
    "extensions.agents.x-k8s.io/v1beta1/SandboxTemplate",
    "agents.x-k8s.io/v1beta1/Sandbox",
}


def _documents(directory: pathlib.Path) -> Iterator[dict[str, Any]]:
    for name in yaml.safe_load((directory / "kustomization.yaml").read_text())["resources"]:
        yield from (d for d in yaml.safe_load_all((directory / name).read_text()) if d)


def _walk(node: Any, key: str) -> Iterator[Any]:
    if isinstance(node, dict):
        for k, value in node.items():
            if k == key:
                yield value
            yield from _walk(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)


def test_every_keyless_attestor_names_its_transparency_log() -> None:
    for policy in _documents(POLICIES):
        for keyless in _walk(policy, "keyless"):
            assert keyless.get("rekor", {}).get("url") or keyless.get("roots"), (
                f"{policy['metadata']['name']}: a keyless attestor without `rekor.url` or `roots`"
                " is refused by Kyverno 1.12"
            )


def test_the_default_set_matches_only_kinds_every_cluster_knows() -> None:
    for policy in _documents(POLICIES):
        for rule in policy["spec"]["rules"]:
            kinds = {kind for found in _walk(rule.get("match", {}), "kinds") for kind in found}
            assert kinds <= KNOWN_KINDS, (
                f"{policy['metadata']['name']}/{rule['name']} matches {sorted(kinds - KNOWN_KINDS)}:"
                " move it to an opt-in component (see `infra/policies/tekton`)"
            )
