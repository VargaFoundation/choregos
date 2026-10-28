"""The session secret reaches the API alone, and the chart can carry a rotation's previous key (S47-01).

The API refuses to start on an empty, published or short `CHOREGOS_SESSION_SECRET`. That check lives
in `create_app()`, not in `Settings`, because the orchestrator workers, the migrations Job and the
trace-drift CronJob build the same `Settings` without the secret: the chart must therefore keep the
secret out of those pods, and these tests hold it there.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
TEMPO = "http://tempo.monitoring.svc:3200"
PREVIOUS = "CHOREGOS_SESSION_SECRET_PREVIOUS"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*sets: str, json_sets: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for value in sets:
        command += ["--set", value]
    for value in json_sets:
        command += ["--set-json", value]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _docs(*sets: str) -> list[dict[str, Any]]:
    rendered = _render(*sets)
    assert rendered.returncode == 0, rendered.stderr[-600:]
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _pods(docs: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    """(workload, container name, container) of every Deployment, Job and CronJob."""
    found: list[tuple[str, str, dict[str, Any]]] = []
    for doc in docs:
        if doc["kind"] in {"Deployment", "Job"}:
            spec = doc["spec"]["template"]["spec"]
        elif doc["kind"] == "CronJob":
            spec = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        else:
            continue
        for container in spec.get("containers", []) + spec.get("initContainers", []):
            found.append((doc["metadata"]["name"], container["name"], container))
    return found


def _env(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in container.get("env") or []}


def test_only_the_api_receives_the_session_secret() -> None:
    docs = _docs(f"global.tracing.tempoUrl={TEMPO}")
    pods = _pods(docs)
    workloads = {workload for workload, _, _ in pods}
    # the processes that build `Settings` and must keep starting without the secret
    assert "choregos-trace-drift" in workloads, workloads
    assert any("migrations" in workload for workload in workloads), workloads
    assert any("orchestrator" in workload for workload in workloads), workloads

    carriers = {
        (workload, name)
        for workload, name, container in pods
        if {"CHOREGOS_SESSION_SECRET", PREVIOUS} & set(_env(container))
    }
    assert carriers == {("choregos-api", "api")}, f"the session secret reached: {sorted(carriers)}"

    (api,) = [c for w, n, c in pods if (w, n) == ("choregos-api", "api")]
    env = _env(api)
    assert env["CHOREGOS_SESSION_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "choregos-api-secrets",
        "key": "session-secret",
    }
    assert env[PREVIOUS]["valueFrom"]["secretKeyRef"] == {
        "name": "choregos-api-secrets",
        "key": "session-secret-previous",
        "optional": True,
    }


def test_extra_env_cannot_shadow_an_api_owned_variable() -> None:
    rendered = _render(json_sets=(f'global.extraEnv=[{{"name":"{PREVIOUS}","value":"x"}}]',))
    assert rendered.returncode != 0, "the chart rendered two values for the previous session key"
    assert PREVIOUS in rendered.stderr
    assert "session-secret-previous" in rendered.stderr, "the error says where to put it instead"


def test_extra_env_still_carries_other_names() -> None:
    """The guard names what the API chart owns; it is not a ban on `global.extraEnv`."""
    extra = [{"name": "CONNECTOR_JIRA_TOKEN", "value": "x"}]
    rendered = _render(json_sets=(f"global.extraEnv={json.dumps(extra)}",))
    assert rendered.returncode == 0, rendered.stderr[-600:]
    assert "CONNECTOR_JIRA_TOKEN" in rendered.stdout


def _external_secret_keys(*sets: str) -> list[str]:
    docs = _docs("global.externalSecrets.enabled=true", *sets)
    (external,) = [
        d for d in docs if d["kind"] == "ExternalSecret" and d["metadata"]["name"] == "choregos-api-secrets"
    ]
    return [item["secretKey"] for item in external["spec"]["data"]]


def test_the_external_secret_carries_the_previous_key_only_when_asked() -> None:
    """A missing vault property fails the whole ExternalSecret sync: the key is opt-in."""
    default = _external_secret_keys()
    assert "session-secret" in default
    assert "session-secret-previous" not in default
    asked = _external_secret_keys("choregos-api.externalSecrets.sessionSecretPrevious=true")
    assert "session-secret-previous" in asked
    assert sorted(set(asked) - {"session-secret-previous"}) == sorted(default), "nothing else changes"


def test_a_bench_secret_is_a_session_secret_the_api_accepts() -> None:
    """`global.devSecrets` draws the secret itself: it must meet the API's 32-byte rule."""
    docs = _docs("global.devSecrets=true", "global.externalSecrets.enabled=false")
    (secret,) = [d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == "choregos-api-secrets"]
    session = secret["stringData"]["session-secret"]
    assert len(session.encode()) >= 32, "the API refuses a deployed session secret shorter than 32 bytes"
    assert "session-secret-previous" not in secret["stringData"], "no rotation on a bench"
