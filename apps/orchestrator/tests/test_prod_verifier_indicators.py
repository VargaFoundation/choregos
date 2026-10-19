# SPDX-License-Identifier: Apache-2.0
"""The production verifier is told what the project can measure in production (S22-48).

On the dev tenant (v0.17.16), `prod-verifier` answered no-go on every release of sandbox-dev #4 and
#6: it asked for before/after figures of errors and latency that the project has no source of (demo
CD, no metrics), while `smoke_ok` was measured true. The rendered prompt now says which indicator
sources the run can read; without any, a measured `smoke_ok` is enough and the verdict says that only
the smoke test was available. With a source, the strict rule stays.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from choregos_api.catalogue import vider_cache
from choregos_api.schemas import AgentSpec
from choregos_contracts import ContextPack, ProjectConfig
from choregos_core.catalogue_d_agents import entree
from choregos_orchestrator.activities.stage import _render_playbook, production_indicator_sources

CATALOGUE = """
outils:
  - name: grafana_query
    description: Reads a Prometheus query through Grafana.
    provider: grafana
    categories: [observability]
    http: { method: GET, url: "https://grafana.example/api/ds/query", query: { q: "{{ query }}" } }
  - name: address_check
    description: Checks a postal address.
    provider: addresses
    categories: [administratif]
    http: { method: GET, url: "https://addresses.example/search", query: { q: "{{ address }}" } }
"""


@pytest.fixture
def tool_catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    path = tmp_path / "catalogue.yaml"
    path.write_text(CATALOGUE, encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(path))
    vider_cache()
    yield
    vider_cache()


def _config(tools: list[str]) -> ProjectConfig:
    return ProjectConfig(
        slug="sandbox-dev", org="diametral", tools=tools, dod={"facts": {"smoke_ok": "true"}}
    )


def _verifier(mcp_servers: list[dict[str, Any]] | None = None) -> Any:
    catalogue_entry = entree("prod-verifier")
    assert catalogue_entry is not None
    spec = AgentSpec.model_validate(
        {"instructions": catalogue_entry.document["spec"]["instructions"], "mcp_servers": mcp_servers or []}
    )
    return SimpleNamespace(agent=SimpleNamespace(slug="prod-verifier"), version=2, spec=spec)


def _prompt(config: ProjectConfig, agent: Any) -> str:
    plan = SimpleNamespace(inputs=[], playbook=None, role="verify_prod")
    item = SimpleNamespace(
        tracker_key="DiametralGroup/choregos-sandbox-dev#4",
        title="Total ignores credit notes",
        body_snapshot="",
        documents={},
        allowed_paths=[],
        fields={},
    )
    bundle = SimpleNamespace(config=config)
    return _render_playbook(plan, bundle, item, ContextPack.empty("q"), agent)  # type: ignore[arg-type]


def test_without_indicator_source_a_measured_smoke_ok_is_enough(tool_catalogue: Any) -> None:
    # The project may call a tool, but not an observability one: no indicator source.
    config = _config(["address_check"])
    assert production_indicator_sources(config, _verifier()) == []
    prompt = _prompt(config, _verifier())
    assert "Production indicators: **none configured for this project**" in prompt
    assert "`smoke_ok: true` is therefore sufficient for a go" in prompt
    assert "Only the smoke test was available (smoke_ok); no service" in prompt
    assert "before and after the release" not in prompt


def test_with_an_observability_tool_the_strict_rule_stays(tool_catalogue: Any) -> None:
    config = _config(["grafana_query", "address_check"])
    assert production_indicator_sources(config, _verifier()) == ["grafana_query"]
    prompt = _prompt(config, _verifier())
    assert "Production indicator sources configured for this project: grafana_query." in prompt
    assert "before and after the release (errors, latency, business errors)" in prompt
    assert "When in doubt, no-go" in prompt
    assert "none configured" not in prompt
    assert "Only the smoke test was available" not in prompt


def test_an_observability_mcp_server_of_the_agent_counts(tool_catalogue: Any) -> None:
    agent = _verifier([{"connector": "grafana-prod", "tools": ["query_prometheus"]}])
    assert production_indicator_sources(_config([]), agent) == ["grafana-prod"]
    assert "configured for this project: grafana-prod." in _prompt(_config([]), agent)
    # A server that is not an observability tool is not an indicator source.
    other = _verifier([{"connector": "github", "tools": ["get_issue"]}])
    assert production_indicator_sources(_config([]), other) == []
