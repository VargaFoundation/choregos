"""An agent's catalogue entry makes tool scripts its default (S57-15, ADR 0122 §9): `spec.tools.scripts`.

The spec accepts and serves it, leaves it out while unset (no published version's digest moves), refuses it
on a remote agent, and a plugin's agent file carries it to the version the installer publishes. Who wins
over it (the deployment, the organisation, its ceiling, the policy) is `apps/orchestrator/tests/
test_tool_scripts.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import AsyncClient
from pydantic import ValidationError

from .test_network_policy_api import AGENTS, LEGACY_DIGEST, LEGACY_SPEC
from .test_plugin_installer import ALONE, ORG, _edit, offline

__all__ = ["offline"]  # the fixture, used by name

A2A = {"kind": "a2a", "card_url": "https://rovo.example/.well-known/agent.json"}


def test_a_version_without_a_default_keeps_its_digest_and_one_with_it_declares_it() -> None:
    from choregos_api.schemas.agents import AgentSpec
    from choregos_api.services.agents import digest

    legacy = AgentSpec.model_validate(LEGACY_SPEC)
    assert "tools" not in legacy.model_dump(mode="json"), "left out while unset"
    assert digest(legacy) == LEGACY_DIGEST
    assert legacy.tool_scripts_default() is None
    declared = AgentSpec.model_validate({**LEGACY_SPEC, "tools": {"scripts": "offer"}})
    assert declared.model_dump(mode="json")["tools"] == {"scripts": "offer"}
    assert digest(declared) != LEGACY_DIGEST, "a declared default is part of what the version IS"
    assert declared.tool_scripts_default() == "offer"


def test_an_unquoted_off_reads_as_off() -> None:
    from choregos_api.schemas.agents import AgentSpec

    raw = yaml.safe_load("tools:\n  scripts: off\n")
    assert raw["tools"]["scripts"] is False, "the YAML reading this test is about"
    assert AgentSpec.model_validate(raw).tool_scripts_default() == "off"


@pytest.mark.parametrize(
    "tools", [{"scripts": "on"}, {"scripts": True}, {}, {"scripts": "offer", "mode": "x"}]
)
def test_a_default_outside_the_contract_is_refused(tools: dict[str, Any]) -> None:
    from choregos_api.schemas.agents import AgentSpec

    with pytest.raises(ValidationError, match="tools"):
        AgentSpec.model_validate({"tools": tools})


def test_a_remote_agent_declares_no_default() -> None:
    from choregos_api.schemas.agents import AgentSpec

    assert AgentSpec.model_validate({"runtime": A2A}).tool_scripts_default() is None
    with pytest.raises(ValidationError, match="no tool scripts"):
        AgentSpec.model_validate({"runtime": A2A, "tools": {"scripts": "offer"}})


async def test_the_registry_publishes_and_serves_the_default(client: AsyncClient, admin: str) -> None:
    spec = {"instructions": "Diagnose the network.", "tools": {"scripts": "offer"}}
    created = await client.post(AGENTS, json={"slug": "net-analyst", "display_name": "N", "spec": spec})
    assert created.status_code == 201, created.text
    version = (await client.get(f"{AGENTS}/net-analyst/versions/1")).json()
    assert version["spec"]["tools"] == {"scripts": "offer"}
    plain = await client.post(
        AGENTS, json={"slug": "plain", "display_name": "P", "spec": {"instructions": "Answer."}}
    )
    assert plain.status_code == 201, plain.text
    assert "tools" not in (await client.get(f"{AGENTS}/plain/versions/1")).json()["spec"]
    refused = await client.post(
        AGENTS, json={"slug": "lister", "display_name": "L", "spec": {"tools": {"scripts": "always"}}}
    )
    assert refused.status_code == 422


async def test_a_plugin_s_agent_file_carries_the_default_to_the_published_version(
    client: AsyncClient, admin: str, offline: Path
) -> None:
    greeter = offline / "hello" / "1.1.0" / "agents" / "greeter.yaml"
    _edit(greeter, lambda d: d["agent"]["spec"].update(tools={"scripts": "always"}))
    refused = await client.post(f"{ORG}/plugins/install", json=ALONE)
    assert refused.status_code == 422, refused.text
    _edit(greeter, lambda d: d["agent"]["spec"].update(tools={"scripts": "offer"}))
    installed = await client.post(f"{ORG}/plugins/install", json=ALONE)
    assert installed.status_code == 201, installed.text
    version = (await client.get(f"{AGENTS}/greeter/versions/1")).json()
    assert version["spec"]["tools"] == {"scripts": "offer"}
