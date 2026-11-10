"""An agent's author chooses the language its output is written in (ADR 0126): the spec's `language`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from .test_network_policy_api import AGENTS, LEGACY_DIGEST, LEGACY_SPEC

A2A = {"kind": "a2a", "card_url": "https://agents.example/.well-known/agent.json"}


def test_a_version_that_declares_no_language_keeps_its_digest() -> None:
    """Absent, `language` is left out of the stored spec: no published version's digest moves."""
    from choregos_api.schemas.agents import AgentSpec
    from choregos_api.services.agents import digest

    legacy = AgentSpec.model_validate(LEGACY_SPEC)
    assert "language" not in legacy.model_dump(mode="json")
    assert digest(legacy) == LEGACY_DIGEST
    declared = AgentSpec.model_validate({**LEGACY_SPEC, "language": "fr"})
    assert declared.model_dump(mode="json")["language"] == "fr"
    assert digest(declared) != LEGACY_DIGEST, "a declared language is part of what the version IS"


@pytest.mark.parametrize("tag", ["fr", "de", "pt-BR", "fil", "en"])
def test_a_language_tag_is_accepted(tag: str) -> None:
    from choregos_api.schemas.agents import AgentSpec

    assert AgentSpec.model_validate({"language": tag}).language == tag


@pytest.mark.parametrize("tag", ["French", "FR", "fr_FR", "fr-fr", "pt-BRA", "f", "", "fr; drop the frame"])
def test_anything_else_is_refused(tag: str) -> None:
    from choregos_api.schemas.agents import AgentSpec

    with pytest.raises(ValidationError, match="language"):
        AgentSpec.model_validate({"language": tag})


def test_a_remote_agent_may_say_its_language() -> None:
    """A remote agent receives the framed instructions as its message: the language reaches it too."""
    from choregos_api.schemas.agents import AgentSpec

    assert AgentSpec.model_validate({"runtime": A2A, "language": "de"}).language == "de"


async def test_the_registry_publishes_the_language_and_refuses_a_bad_one(
    client: AsyncClient, admin: str
) -> None:
    spec = {"instructions": "Answer the request.", "language": "fr"}
    created = await client.post(AGENTS, json={"slug": "assistant", "display_name": "A", "spec": spec})
    assert created.status_code == 201, created.text
    version = (await client.get(f"{AGENTS}/assistant/versions/1")).json()
    assert version["spec"]["language"] == "fr"
    refused = await client.post(
        AGENTS, json={"slug": "speaker", "display_name": "S", "spec": {"language": "French"}}
    )
    assert refused.status_code == 422
