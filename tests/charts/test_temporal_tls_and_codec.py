"""Temporal over TLS and its payload codec reach the API and every worker (S80-06, ADR 0183).

The chart mounts the TLS Secret and the codec Secret into the pods that hold a Temporal client, and
sets the variables `choregos_core.temporal_connection` reads. Off by default: nothing is mounted and
no variable is set, so dev, kind and compose keep talking in clear.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")

ON = (
    "global.temporal.tls.enabled=true",
    "global.temporal.tls.secretName=temporal-client",
    "global.temporal.tls.mutual=true",
    "global.temporal.tls.serverName=frontend.temporal.internal",
    "global.temporal.codec.enabled=true",
    "global.temporal.codec.secretName=temporal-codec",
)


def _render(*sets: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    args = ["helm", "template", "choregos", str(CHART), "-f", str(CHART / "values" / "local.yaml")]
    for s in sets:
        args += ["--set", s]
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=120)


def _temporal_clients(*sets: str) -> dict[str, dict[str, Any]]:
    """Each Deployment holding a Temporal client (the API, one per worker queue), by name."""
    docs = [d for d in yaml.safe_load_all(_render(*sets).stdout) if d]
    pods: dict[str, dict[str, Any]] = {}
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        name = doc["metadata"]["name"]
        if name == "choregos-api" or name.startswith("choregos-orchestrator-"):
            pods[name] = doc["spec"]["template"]["spec"]
    return pods


def _env(spec: dict[str, Any]) -> dict[str, str]:
    (container,) = [c for c in spec["containers"] if c["name"] in ("api", "worker")]
    return {v["name"]: v.get("value", "") for v in container.get("env", [])}


def _mounts(spec: dict[str, Any]) -> dict[str, str]:
    (container,) = [c for c in spec["containers"] if c["name"] in ("api", "worker")]
    return {m["name"]: m["mountPath"] for m in container.get("volumeMounts", [])}


def _secret_volumes(spec: dict[str, Any]) -> dict[str, str]:
    return {v["name"]: v["secret"]["secretName"] for v in spec.get("volumes", []) if "secret" in v}


def test_off_by_default_nothing_is_mounted_and_no_variable_is_set() -> None:
    pods = _temporal_clients()
    assert "choregos-api" in pods and any(n.startswith("choregos-orchestrator-") for n in pods)
    for name, spec in pods.items():
        assert not [
            k for k in _env(spec) if k.startswith(("CHOREGOS_TEMPORAL_TLS", "CHOREGOS_TEMPORAL_CODEC"))
        ]
        assert "temporal-tls" not in _mounts(spec) and "temporal-codec" not in _mounts(spec), name


def test_enabled_the_api_and_every_worker_mount_both_secrets_and_name_the_files() -> None:
    pods = _temporal_clients(*ON)
    assert len(pods) >= 2
    for name, spec in pods.items():
        assert (
            _secret_volumes(spec).items()
            >= {
                "temporal-tls": "temporal-client",
                "temporal-codec": "temporal-codec",
            }.items()
        ), name
        mounts = _mounts(spec)
        assert mounts["temporal-tls"] == "/etc/choregos/temporal-tls", name
        assert mounts["temporal-codec"] == "/etc/choregos/temporal-codec", name
        env = _env(spec)
        assert env["CHOREGOS_TEMPORAL_TLS"] == "true", name
        assert env["CHOREGOS_TEMPORAL_TLS_CA_FILE"] == "/etc/choregos/temporal-tls/ca.crt", name
        assert env["CHOREGOS_TEMPORAL_TLS_CERT_FILE"] == "/etc/choregos/temporal-tls/tls.crt", name
        assert env["CHOREGOS_TEMPORAL_TLS_KEY_FILE"] == "/etc/choregos/temporal-tls/tls.key", name
        assert env["CHOREGOS_TEMPORAL_TLS_SERVER_NAME"] == "frontend.temporal.internal", name
        assert env["CHOREGOS_TEMPORAL_CODEC_KEYS_FILE"] == "/etc/choregos/temporal-codec/keys", name


def test_without_mutual_tls_no_client_certificate_is_named() -> None:
    for spec in _temporal_clients(
        "global.temporal.tls.enabled=true", "global.temporal.tls.secretName=ca"
    ).values():
        env = _env(spec)
        assert env["CHOREGOS_TEMPORAL_TLS_CA_FILE"].endswith("/ca.crt")
        assert "CHOREGOS_TEMPORAL_TLS_CERT_FILE" not in env and "CHOREGOS_TEMPORAL_TLS_KEY_FILE" not in env
        assert "temporal-codec" not in _mounts(spec)


@pytest.mark.parametrize(
    ("sets", "named"),
    [
        (("global.temporal.tls.enabled=true",), "global.temporal.tls.secretName"),
        (("global.temporal.codec.enabled=true",), "global.temporal.codec.secretName"),
        (
            (*ON, "global.extraEnv[0].name=CHOREGOS_TEMPORAL_CODEC_KEYS", "global.extraEnv[0].value=x"),
            "CHOREGOS_TEMPORAL_CODEC_KEYS",
        ),
    ],
)
def test_a_switch_without_its_secret_or_a_second_value_is_refused(sets: tuple[str, ...], named: str) -> None:
    refused = _render(*sets, check=False)
    assert refused.returncode != 0
    assert named in refused.stderr
