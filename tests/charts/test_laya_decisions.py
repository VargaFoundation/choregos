"""S52-02 (ADR 0121): Laya, a self-hosted decision model, behind the embedded LiteLLM as `platform/decide`.

The chart renders it only when `global.decisions.embedded` asks; LiteLLM then routes the alias to
Laya's Service with Laya's bearer and an internal price, the entries it already served render as
before, and only LiteLLM's pods reach Laya.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
GATEWAY = ("global.gateway.embedded=true",)
DECISIONS = (*GATEWAY, "global.decisions.embedded=true")

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")

# What `charts/litellm/templates/deployment.yaml` rendered for the default `models` before S52-02,
# byte for byte: an optional per-model field must not move a line of an entry that does not set it.
DEFAULT_MODEL_LIST = """model_list:
  - model_name: platform/standard
    litellm_params:
      model: openai/gpt-5.5
  - model_name: platform/strong
    litellm_params:
      model: openai/gpt-5.5
  - model_name: platform/cheap
    litellm_params:
      model: openai/gpt-5.4-mini
  - model_name: platform/aux
    litellm_params:
      model: openai/gpt-5.4-mini
  - model_name: platform/embed
    litellm_params:
      model: openai/text-embedding-3-small
    model_info: { mode: embedding }
"""


def _render(*overrides: str) -> list[dict[str, Any]]:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _one(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    (found,) = [d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name]
    return found


def _config_text(documents: list[dict[str, Any]]) -> str:
    return str(_one(documents, "ConfigMap", "choregos-litellm")["data"]["config.yaml"])


def _models(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    config = yaml.safe_load(_config_text(documents))
    return {entry["model_name"]: entry for entry in config["model_list"]}


def _env(container: dict[str, Any]) -> dict[str, Any]:
    return {e["name"]: e.get("value", e.get("valueFrom")) for e in container.get("env", [])}


def _model_list_block(text: str) -> str:
    match = re.search(r"^model_list:\n(?:^  .*\n)*", text, flags=re.MULTILINE)
    assert match, text
    return match.group(0)


def test_platform_decide_appears_only_when_the_decision_model_is_embedded() -> None:
    without = _render(*GATEWAY)
    assert "platform/decide" not in _models(without)
    assert not [d for d in without if d["metadata"]["name"] == "choregos-laya"]

    decide = _models(_render(*DECISIONS))["platform/decide"]
    # Laya speaks TypeSafe Jev's wire; LiteLLM 1.105.5's `typesafe` provider posts to
    # `<api_base>/v1/systemone` with `model: multilingual` and the key as a bearer.
    assert decide["litellm_params"] == {
        "model": "typesafe/multilingual",
        "api_base": "http://choregos-laya:8000",
        "api_key": "os.environ/LAYA_API_KEY",
    }
    # ADR 0004: an internal price, read back by the catalogue sync from `model_info`. A float, not
    # the string `2e-08` that a bare Helm number would render and YAML would not read as one.
    assert decide["model_info"] == {
        "mode": "evaluation",
        "input_cost_per_token": pytest.approx(2e-08),
        "output_cost_per_token": 0.0,
    }
    assert isinstance(decide["model_info"]["input_cost_per_token"], float)


def test_the_internal_price_is_the_one_configured() -> None:
    decide = _models(_render(*DECISIONS, "global.decisions.inputCostPerToken=0.0000005"))["platform/decide"]
    assert decide["model_info"]["input_cost_per_token"] == pytest.approx(5e-07)


def test_the_entries_litellm_already_served_render_byte_for_byte_as_before() -> None:
    for overrides in (GATEWAY, DECISIONS):
        assert _model_list_block(_config_text(_render(*overrides))).startswith(DEFAULT_MODEL_LIST)
    assert _model_list_block(_config_text(_render(*GATEWAY))) == DEFAULT_MODEL_LIST

    upstream = (
        "choregos-litellm.upstream.apiBase=https://gw.example",
        "choregos-litellm.upstream.apiKeySecret=upstream",
    )
    models = _models(_render(*DECISIONS, *upstream))
    assert models["platform/standard"]["litellm_params"] == {
        "model": "openai/gpt-5.5",
        "api_base": "https://gw.example",
        "api_key": "os.environ/UPSTREAM_API_KEY",
    }
    # The decision model keeps its own endpoint and key whatever the upstream is.
    assert models["platform/decide"]["litellm_params"]["api_base"] == "http://choregos-laya:8000"
    assert models["platform/decide"]["litellm_params"]["api_key"] == "os.environ/LAYA_API_KEY"


def test_a_model_may_name_its_own_endpoint_key_and_price() -> None:
    entry = "choregos-litellm.models[0]"
    models = _models(
        _render(
            *GATEWAY,
            "choregos-litellm.upstream.apiBase=https://gw.example",
            f"{entry}.alias=platform/local",
            f"{entry}.model=hosted_vllm/qwen",
            f"{entry}.apiBase=http://vllm:8000/v1",
            f"{entry}.apiKeyEnv=VLLM_KEY",
            f"{entry}.inputCostPerToken=0.000001",
            f"{entry}.outputCostPerToken=0.000002",
        )
    )
    assert models["platform/local"]["litellm_params"] == {
        "model": "hosted_vllm/qwen",
        "api_base": "http://vllm:8000/v1",
        "api_key": "os.environ/VLLM_KEY",
    }
    assert models["platform/local"]["model_info"] == {
        "input_cost_per_token": pytest.approx(1e-06),
        "output_cost_per_token": pytest.approx(2e-06),
    }


def test_litellm_holds_laya_s_bearer_and_laya_asks_for_it() -> None:
    documents = _render(*DECISIONS)
    (litellm,) = _one(documents, "Deployment", "choregos-litellm")["spec"]["template"]["spec"]["containers"]
    (laya,) = _one(documents, "Deployment", "choregos-laya")["spec"]["template"]["spec"]["containers"]
    reference = {"secretKeyRef": {"name": "choregos-laya", "key": "api-key"}}
    assert _env(litellm)["LAYA_API_KEY"] == reference
    assert _env(laya)["LAYA_API_KEY"] == reference
    secret = _one(documents, "Secret", "choregos-laya")
    assert len(secret["stringData"]["api-key"]) >= 32

    provided = _render(*DECISIONS, "global.decisions.apiKeySecret=decisions-key")
    assert not [d for d in provided if d["kind"] == "Secret" and d["metadata"]["name"] == "choregos-laya"]
    (litellm,) = _one(provided, "Deployment", "choregos-litellm")["spec"]["template"]["spec"]["containers"]
    assert _env(litellm)["LAYA_API_KEY"] == {"secretKeyRef": {"name": "decisions-key", "key": "api-key"}}

    without = _render(*GATEWAY)
    (litellm,) = _one(without, "Deployment", "choregos-litellm")["spec"]["template"]["spec"]["containers"]
    assert "LAYA_API_KEY" not in _env(litellm)


def test_laya_runs_offline_on_cpu_and_is_probed_on_its_health_route() -> None:
    deployment = _one(_render(*DECISIONS), "Deployment", "choregos-laya")
    pod = deployment["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    env = _env(container)
    # The checkpoint is in the image (docker/laya.Dockerfile): nothing is fetched from the Hub.
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["LAYA_DEVICE"] == "cpu"
    # The tag follows the chart's `global.imageTag`, which `tools/bump_version.py` moves at each release.
    tag = yaml.safe_load((CHART / "values.yaml").read_text())["global"]["imageTag"]
    assert container["image"] == f"ghcr.io/vargafoundation/choregos-laya:{tag}"
    assert container["ports"] == [{"name": "http", "containerPort": 8000}]
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"] == {"path": "/health", "port": "http"}, probe
    assert "nvidia.com/gpu" not in container["resources"].get("limits", {})
    # The strict Kyverno `non-root` rule (test_pod_security.py) on this pod too.
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}


def _selects(selector: dict[str, Any], labels: dict[str, str]) -> bool:
    """Whether a label selector (matchLabels and matchExpressions) selects `labels`."""
    if any(labels.get(key) != value for key, value in (selector.get("matchLabels") or {}).items()):
        return False
    for expression in selector.get("matchExpressions") or []:
        key, operator, values = expression["key"], expression["operator"], expression.get("values", [])
        if operator == "In" and labels.get(key) not in values:
            return False
        if operator == "NotIn" and labels.get(key) in values:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator == "DoesNotExist" and key in labels:
            return False
    return True


def test_only_litellm_s_pods_reach_laya() -> None:
    documents = _render(*DECISIONS)
    laya_pod = _one(documents, "Deployment", "choregos-laya")["spec"]["template"]["metadata"]["labels"]
    litellm_pod = _one(documents, "Deployment", "choregos-litellm")["spec"]["template"]["metadata"]["labels"]
    api_pod = _one(documents, "Deployment", "choregos-api")["spec"]["template"]["metadata"]["labels"]

    policy = _one(documents, "NetworkPolicy", "choregos-laya")["spec"]
    assert _selects(policy["podSelector"], laya_pod)
    assert set(policy["policyTypes"]) == {"Ingress", "Egress"}
    (rule,) = policy["ingress"]
    (source,) = rule["from"]
    assert set(source) == {"podSelector"}, "no namespaceSelector, no ipBlock: this namespace's LiteLLM only"
    assert _selects(source["podSelector"], litellm_pod)
    assert not _selects(source["podSelector"], api_pod)
    assert rule["ports"] == [{"protocol": "TCP", "port": 8000}]
    # Its checkpoint is in the image: Laya calls nothing.
    assert policy["egress"] == []

    # NetworkPolicies add up: no other one may admit more to Laya's pod.
    others = [
        d["metadata"]["name"]
        for d in documents
        if d["kind"] == "NetworkPolicy"
        and d["metadata"]["name"] != "choregos-laya"
        and "Ingress" in d["spec"].get("policyTypes", [])
        and _selects(d["spec"]["podSelector"], laya_pod)
    ]
    assert others == [], others


def test_litellm_may_leave_its_namespace_s_egress_lock_for_laya() -> None:
    """`choregos-allow-platform` locks the egress of the platform's pods, LiteLLM's included."""

    def laya_rules(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        egress = _one(documents, "NetworkPolicy", "choregos-allow-platform")["spec"]["egress"]
        return [
            rule
            for rule in egress
            if any(
                (peer.get("podSelector") or {}).get("matchLabels", {}).get("app.kubernetes.io/name")
                == "choregos-laya"
                for peer in rule["to"]
            )
        ]

    assert laya_rules(_render(*GATEWAY)) == []
    (rule,) = laya_rules(_render(*DECISIONS))
    assert rule["ports"] == [{"protocol": "TCP", "port": 8000}]


def test_laya_without_the_embedded_gateway_is_refused() -> None:
    command = ["helm", "template", "choregos", str(CHART), "--set", "global.decisions.embedded=true"]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    assert rendered.returncode != 0
    assert "global.decisions.embedded needs global.gateway.embedded" in rendered.stderr


def test_the_image_is_offline_and_its_laya_is_the_locked_one() -> None:
    dockerfile = (ROOT / "docker" / "laya.Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("AS runtime", 1)[1]
    assert "HF_HUB_OFFLINE=1" in runtime
    assert "--require-hashes" in dockerfile
    pinned = re.search(r"^laya\[serve\]==(\S+)$", (ROOT / "docker/laya/requirements.in").read_text(), re.M)
    assert pinned, "requirements.in must pin laya[serve]"
    locked = (ROOT / "docker/laya/requirements.txt").read_text(encoding="utf-8")
    assert f"\nlaya=={pinned.group(1)} \\" in locked, "run `make laya-lock`"
    assert re.search(r"^torch==\S+\+cpu ", locked, re.M), "the CPU build of torch, never CUDA wheels"


def test_the_live_stack_runs_the_chart_s_configuration_and_the_local_install_s() -> None:
    """`tests/live/decisions/litellm.yaml` is what the live test proves; it must stay what the chart
    renders (the Service name aside) and what `compose.yaml`'s `decisions` profile writes."""
    from choregos_api.local_demo import litellm_config

    live = yaml.safe_load((ROOT / "tests/live/decisions/litellm.yaml").read_text(encoding="utf-8"))
    rendered = yaml.safe_load(_config_text(_render(*DECISIONS)))
    chart = _models(_render(*DECISIONS))["platform/decide"]
    chart["litellm_params"]["api_base"] = "http://laya:8000"
    (decide,) = live["model_list"]
    assert decide == chart
    for section in ("litellm_settings", "general_settings", "guardrails"):
        assert live[section] == rendered[section], section

    local = {m["model_name"]: m for m in litellm_config(None, decide=True)["model_list"]}
    assert local["platform/decide"] == decide
