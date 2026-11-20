"""An embedded component adopts a Service name and a volume that already exist (S80-16, ADR 0195).

An operator whose clients already dial `litellm` and `ecphoria`, and whose data already sits in a claim,
switches onto the chart by values: the names and the claim are kept, no claim is rendered over the
existing one, and every URL the platform builds follows the name.
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

EMBEDDED = ("global.gateway.embedded=true", "global.memory.embedded=true")


def _docs(*sets: str) -> list[dict[str, Any]]:
    cmd = ["helm", "template", "c", str(CHART)]
    for s in (*EMBEDDED, *sets):
        cmd += ["--set", s]
    out = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _named(docs: list[dict[str, Any]], kind: str) -> set[str]:
    return {d["metadata"]["name"] for d in docs if d["kind"] == kind}


def test_defaults_name_everything_after_the_release() -> None:
    docs = _docs()
    assert {"c-litellm", "c-ecphoria"} <= _named(docs, "Service")
    assert "c-ecphoria" in _named(docs, "PersistentVolumeClaim")


def test_the_services_keep_the_names_clients_already_dial_and_the_urls_follow() -> None:
    docs = _docs("global.gateway.serviceName=litellm", "global.memory.serviceName=ecphoria")
    assert {"litellm", "ecphoria"} <= _named(docs, "Service")
    assert not {"c-litellm", "c-ecphoria"} & _named(docs, "Service")
    api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "choregos-api")
    env = {e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["CHOREGOS_MEMORY_URL"] == "http://ecphoria:8432"
    assert env["CHOREGOS_GATEWAY_URL"] == "http://litellm:4000"
    ecphoria = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "c-ecphoria")
    envs = {e["name"]: e.get("value") for e in ecphoria["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert envs["ECPHORIA_EMBEDDING__OPENAI_BASE_URL"] == "http://litellm:4000/v1"


def test_an_existing_claim_is_mounted_and_no_claim_is_rendered_over_it() -> None:
    docs = _docs("choregos-ecphoria.persistence.existingClaim=ecphoria-data")
    assert "c-ecphoria" not in _named(docs, "PersistentVolumeClaim")
    ecphoria = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "c-ecphoria")
    (data,) = [v for v in ecphoria["spec"]["template"]["spec"]["volumes"] if v["name"] == "data"]
    assert data["persistentVolumeClaim"]["claimName"] == "ecphoria-data"
