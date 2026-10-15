"""The key that seals connector secrets reaches the API and the orchestrator (S39-06c, ADR 0058).

Both read the sealed values: the API to serve a connector, the orchestrator to build a project's
adapters. The key is OPTIONAL in the pod spec (its absence switches the feature off and the pod
still starts), and a bench that writes its own secrets draws one.
"""

from __future__ import annotations

import base64
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*sets: str, env: str = "local") -> list[dict[str, Any]]:
    args = ["helm", "template", "choregos", str(CHART), "-f", str(CHART / "values" / f"{env}.yaml")]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _keks_refs(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for var in container.get("env", []):
                if var["name"] == "CHOREGOS_SECRETS_KEKS":
                    refs[doc["metadata"]["name"]] = var["valueFrom"]["secretKeyRef"]
    return refs


def test_the_api_and_every_orchestrator_deployment_receive_the_key_as_an_optional_secret_key() -> None:
    refs = _keks_refs(_render())
    assert any("api" in name for name in refs)
    assert any("orchestrator" in name or "worker" in name for name in refs)
    for name, ref in refs.items():
        assert ref == {"name": "choregos-api-secrets", "key": "secrets-keks", "optional": True}, name


def test_the_secret_and_the_entry_are_configurable() -> None:
    holder, entry = "custom-holder", "custom-entry"
    refs = _keks_refs(_render(f"global.secrets.keksSecretRef={holder}", f"global.secrets.keksKey={entry}"))
    assert refs and all(r == {"name": holder, "key": entry, "optional": True} for r in refs.values())


def test_a_bench_draws_a_valid_key_that_the_platform_parses() -> None:
    from choregos_api.secret_store import parse_keyring

    (secret,) = [
        d
        for d in _render("global.devSecrets=true")
        if d.get("kind") == "Secret" and d["metadata"]["name"] == "choregos-api-secrets"
    ]
    keks = secret["stringData"]["secrets-keks"]
    ring = parse_keyring(keks)
    assert ring is not None and ring.active_id == "k1"
    assert len(base64.b64decode(keks.split(":", 1)[1])) == 32


def test_the_external_secret_lists_the_key_only_when_asked() -> None:
    def keys(*sets: str) -> list[str]:
        (es,) = [d for d in _render(*sets, env="staging") if d.get("kind") == "ExternalSecret"]
        return [item["secretKey"] for item in es["spec"]["data"]]

    assert "secrets-keks" not in keys()
    assert keys("global.secrets.keksInExternalSecret=true")[-1] == "secrets-keks"
