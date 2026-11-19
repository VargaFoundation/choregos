"""The embedded LiteLLM takes what an operator's gateway needs (S80-14, ADR 0195).

Per-model endpoints and price sheets (cache prices, tiers), the database's host, user, password Secret
and `sslmode`, the guard and the spans callback as switches (on by default), the image's local price
sheet, extra secret variables and the pod's security context.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
L = "choregos-litellm"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*sets: str, values: str | None = None) -> dict[str, dict[str, Any]]:
    cmd = ["helm", "template", "c", str(CHART), "--set", "global.gateway.embedded=true"]
    for s in sets:
        cmd += ["--set", s]
    if values:
        cmd += ["-f", values]
    out = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return {
        d["kind"]: d
        for d in yaml.safe_load_all(out.stdout)
        if d and d.get("metadata", {}).get("name") == "c-litellm"
    }


def _config(docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return yaml.safe_load(docs["ConfigMap"]["data"]["config.yaml"])


def _container(docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return docs["Deployment"]["spec"]["template"]["spec"]["containers"][0]


def _env(docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {e["name"]: e for e in _container(docs)["env"]}


def test_defaults_keep_the_guard_the_spans_and_the_pod_context() -> None:
    docs = _render()
    cfg = _config(docs)
    assert [g["guardrail_name"] for g in cfg["guardrails"]] == ["choregos-secrets"]
    assert cfg["litellm_settings"]["callbacks"] == ["choregos_llm_spans.handler"]
    pod = docs["Deployment"]["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsUser"] == 1000 and pod["securityContext"]["runAsNonRoot"] is True
    assert _container(docs)["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert "LITELLM_LOCAL_MODEL_COST_MAP" not in _env(docs)
    assert "sslmode=prefer" in _env(docs)["DATABASE_URL"]["value"]


def test_the_guard_and_the_spans_can_be_switched_off_and_the_gateway_rolls() -> None:
    on = _render()["Deployment"]["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
    docs = _render(f"{L}.guardrail.enabled=false", f"{L}.spans.enabled=false")
    cfg = _config(docs)
    assert "guardrails" not in cfg and "callbacks" not in cfg["litellm_settings"]
    assert docs["Deployment"]["spec"]["template"]["metadata"]["annotations"]["checksum/config"] != on


def test_a_model_carries_its_endpoint_its_key_variable_and_its_whole_price_sheet(
    tmp_path: pathlib.Path,
) -> None:
    values = tmp_path / "v.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                L: {
                    "extraSecretEnv": [
                        {"name": "PLATFORM_LLM_KEY", "secretName": "gw", "key": "platform-llm-key"}
                    ],
                    "models": [
                        {
                            "alias": "platform/claude-haiku",
                            "model": "anthropic/choregos-claude-haiku",
                            "apiBase": "http://gw.inference.svc:4000",
                            "apiKeyEnv": "PLATFORM_LLM_KEY",
                            "inputCostPerToken": 0.0000001,
                            "outputCostPerToken": 0.0000005,
                            "modelInfo": {
                                "cache_read_input_token_cost": 0.00000001,
                                "tiered_pricing": [{"range": [0, 100000], "input_cost_per_token": 0.0000001}],
                            },
                        }
                    ],
                }
            }
        )
    )
    docs = _render(values=str(values))
    (model,) = _config(docs)["model_list"]
    assert model["litellm_params"]["api_base"] == "http://gw.inference.svc:4000"
    assert model["litellm_params"]["api_key"] == "os.environ/PLATFORM_LLM_KEY"
    info = model["model_info"]
    assert float(info["input_cost_per_token"]) == pytest.approx(1e-7)
    assert float(info["cache_read_input_token_cost"]) == pytest.approx(1e-8)
    assert info["tiered_pricing"][0]["range"] == [0, 100000]
    assert _env(docs)["PLATFORM_LLM_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "gw",
        "key": "platform-llm-key",
    }


def test_the_database_takes_its_own_host_user_secret_and_sslmode() -> None:
    docs = _render(
        f"{L}.database.host=choregos-pg",
        f"{L}.database.user=litellm",
        f"{L}.database.passwordSecret.name=litellm.pg.credentials",
        f"{L}.database.sslmode=require",
    )
    env = _env(docs)
    url = env["DATABASE_URL"]["value"]
    assert url.startswith("postgresql://litellm:$(DATABASE_PASSWORD)@choregos-pg:") and url.endswith(
        "/litellm?sslmode=require"
    )
    assert env["DATABASE_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": "litellm.pg.credentials",
        "key": "password",
    }


def test_the_local_price_sheet_the_image_digest_and_a_relaxed_pod_context() -> None:
    digest = "v1.105.5@sha256:" + "a" * 64
    docs = _render(
        f"{L}.localModelCostMap=true",
        f"{L}.image.tag={digest}",
        f"{L}.podSecurityContext.runAsNonRoot=false",
        f"{L}.podSecurityContext.runAsUser=0",
    )
    assert _env(docs)["LITELLM_LOCAL_MODEL_COST_MAP"]["value"] == "True"
    assert _container(docs)["image"].endswith(digest)
    pod = docs["Deployment"]["spec"]["template"]["spec"]["securityContext"]
    assert pod["runAsNonRoot"] is False and pod["runAsUser"] == 0
