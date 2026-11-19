"""S39-07a: the chart tells every process which gateway adapter the deployment uses."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _gateway_types(*overrides: str) -> set[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    found: set[str] = set()
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for env in container.get("env", []):
                if env["name"] == "CHOREGOS_GATEWAY_TYPE":
                    found.add(env["value"])
    return found


def test_litellm_is_the_default_gateway_type() -> None:
    assert _gateway_types() == {"litellm"}


def test_the_configured_gateway_type_reaches_the_pods() -> None:
    assert _gateway_types("global.gateway.type=openai-compatible") == {"openai-compatible"}


def _gateway_key(*overrides: str) -> dict[str, str]:
    """The mode and the secret key the API's pod reads its gateway credential from."""
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    found: dict[str, str] = {}
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for env in container.get("env", []):
                if env["name"] == "CHOREGOS_GATEWAY_KEY_MODE":
                    found[f"{doc['metadata']['name']}:mode"] = env["value"]
                if env["name"] == "CHOREGOS_GATEWAY_MASTER_KEY":
                    found[f"{doc['metadata']['name']}:key"] = env["valueFrom"]["secretKeyRef"]["key"]
    return found


def test_the_master_key_mode_is_the_default() -> None:
    found = _gateway_key()
    assert found and {v for k, v in found.items() if k.endswith(":mode")} == {"master"}
    assert {v for k, v in found.items() if k.endswith(":key")} == {"master-key"}


def test_a_delegated_gateway_reads_the_delegation_key_everywhere() -> None:
    found = _gateway_key("global.gateway.keyMode=delegated")
    assert {v for k, v in found.items() if k.endswith(":mode")} == {"delegated"}
    keys = {k: v for k, v in found.items() if k.endswith(":key")}
    assert keys and set(keys.values()) == {"delegation-key"}, keys


def _refused(*overrides: str) -> str:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rendered.returncode != 0, "the chart must refuse this combination"
    return rendered.stderr


def test_delegation_is_refused_with_the_embedded_gateway_and_an_unknown_mode() -> None:
    assert "embedded" in _refused("global.gateway.keyMode=delegated", "global.gateway.embedded=true")
    assert "master or delegated" in _refused("global.gateway.keyMode=root")
