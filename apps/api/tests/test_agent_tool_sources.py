"""An agent version names the tools it reaches, source by source (S60-01, ADR 0127): `spec.tools`
`catalogue`, `extensions` and `web`.

The spec accepts and serves each list, leaves it out while unset (no published version's digest moves),
refuses a name outside the contract, and keeps a tool-scripts default on its own valid. What a run is
offered from these lists is S60-02's.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from .test_network_policy_api import LEGACY_DIGEST, LEGACY_SPEC


def test_a_version_that_names_no_source_keeps_its_digest() -> None:
    from choregos_api.schemas.agents import AgentSpec
    from choregos_api.services.agents import digest

    legacy = AgentSpec.model_validate(LEGACY_SPEC)
    assert "tools" not in legacy.model_dump(mode="json")
    assert digest(legacy) == LEGACY_DIGEST
    scripts_only = AgentSpec.model_validate({**LEGACY_SPEC, "tools": {"scripts": "offer"}})
    assert scripts_only.model_dump(mode="json")["tools"] == {"scripts": "offer"}, "no source written"


def test_a_version_that_names_its_sources_declares_them_even_empty() -> None:
    from choregos_api.schemas.agents import AgentSpec
    from choregos_api.services.agents import digest

    tools = {"catalogue": ["get_metrics", "group:observability"], "extensions": [], "web": ["search"]}
    declared = AgentSpec.model_validate({**LEGACY_SPEC, "tools": tools})
    assert declared.model_dump(mode="json")["tools"] == {"scripts": "off", **tools}
    assert declared.tool_scripts_default() == "off", "a version naming its sources sets no scripts default"
    assert digest(declared) != LEGACY_DIGEST, "what a version reaches is part of what it IS"


@pytest.mark.parametrize(
    "tools",
    [
        {"catalogue": ["Get Metrics"]},
        {"catalogue": ["group:"]},
        {"extensions": ["../evil"]},
        {"web": ["browse"]},
        {"web": ["fetch", "fetch"]},
        {"catalogue": "get_metrics"},
    ],
)
def test_a_source_outside_the_contract_is_refused(tools: dict[str, Any]) -> None:
    from choregos_api.schemas.agents import AgentSpec

    with pytest.raises(ValidationError, match="tools"):
        AgentSpec.model_validate({"tools": tools})


def test_a_remote_agent_names_no_source() -> None:
    from choregos_api.schemas.agents import AgentSpec

    a2a = {"kind": "a2a", "card_url": "https://rovo.example/.well-known/agent.json"}
    with pytest.raises(ValidationError, match="a2a"):
        AgentSpec.model_validate({"runtime": a2a, "tools": {"web": []}})
