"""The `cilium` connector's read-only identity (ADR 0052): off by default; on, a service account
whose only rights are get/list/watch on Cilium's two policy kinds, and a token Secret for it.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
NAME = "choregos-netops-cilium-reader"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _documents(*overrides: str) -> list[dict[str, Any]]:
    command = ["helm", "template", "choregos", str(CHART), "--namespace", "choregos"]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rendered.returncode == 0, rendered.stderr
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _ours(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {d["kind"]: d for d in documents if d.get("metadata", {}).get("name", "").startswith(NAME)}


def test_nothing_by_default() -> None:
    assert _ours(_documents()) == {}


def test_enabled_it_reads_cilium_policies_and_nothing_else() -> None:
    ours = _ours(_documents("netops.ciliumReader.enabled=true"))
    assert set(ours) == {"ServiceAccount", "ClusterRole", "ClusterRoleBinding", "Secret"}
    assert ours["ServiceAccount"]["automountServiceAccountToken"] is False
    assert ours["ClusterRole"]["rules"] == [
        {
            "apiGroups": ["cilium.io"],
            "resources": ["ciliumnetworkpolicies", "ciliumclusterwidenetworkpolicies"],
            "verbs": ["get", "list", "watch"],
        }
    ], "no write verb, no other resource"
    binding = ours["ClusterRoleBinding"]
    assert binding["roleRef"]["name"] == NAME
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": NAME, "namespace": "choregos"}]
    secret = ours["Secret"]
    assert secret["type"] == "kubernetes.io/service-account-token"
    assert secret["metadata"]["annotations"] == {"kubernetes.io/service-account.name": NAME}


def test_the_token_secret_can_be_left_to_someone_else() -> None:
    ours = _ours(_documents("netops.ciliumReader.enabled=true", "netops.ciliumReader.tokenSecret=false"))
    assert "Secret" not in ours and "ClusterRole" in ours
