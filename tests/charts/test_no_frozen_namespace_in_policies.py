"""No admission policy references the deleted `ghcr.io/vargafoundation` namespace.

On 2026-11-21 every `choregos-*`, `choregos-agents/*`, `charts/choregos*`, `ecphoria` and
`ecphoria-operator` package of the frozen namespace was deleted, after checking that no workload used
them. The Kyverno entries that verified them (signers `VargaFoundation/choregos` and
`VargaFoundation/ecphoria`) and the plugin-server alternative were dead, and are gone.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICIES = sorted((ROOT / "infra" / "policies").rglob("*.yaml"))


def test_policies_are_found() -> None:
    names = {path.name for path in POLICIES}
    assert {"images.yaml", "plugin-servers.yaml"} <= names


def test_no_policy_references_the_frozen_namespace() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in POLICIES
        if "vargafoundation" in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == [], "the frozen namespace was deleted on 2026-11-21: " + ", ".join(offenders)
