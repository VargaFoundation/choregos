"""An assistant's conversation sends its agent's instructions rendered (S63-27, #1737, ADR 0133).

The agent's template reads `{{ config.house_name }}` (the installation's value), `{{ config.desk }}` (a
default of the test's `config_schema`) and `{{ preferences.risk_tolerance }}` (the caller's `user` profile
memory). The first model request's system message carries the values, never the braces; a resumed
conversation takes the installation's config as it is now.
"""

# ruff: noqa: F811 - a fixture imported from another module is redefined by the parameter that names it

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from choregos_core import utcnow
from httpx import AsyncClient
from sqlalchemy import select

from .kits.assistant_install import FIXTURE, ORG
from .kits.assistant_memory import _agent, _assistant, _open
from .kits.conversations import _events
from .kits.synthetic_installation import synthetic_installation  # noqa: F401
from .test_agent_sessions import Upstream, upstream  # noqa: F401

pytestmark = pytest.mark.usefixtures("synthetic_installation")

TEMPLATE = (
    "You serve {{ config.house_name }} from the {{ config.desk }} desk. "
    "The person's risk tolerance is {{ preferences.risk_tolerance }}."
    "{% if preferences.horizon_years %} Horizon: {{ preferences.horizon_years }} years.{% endif %}"
)


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    """The fixture plugin, its `config_schema` given a `desk` key with a default."""
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    target = root / "investment-assistant" / "0.1.0"
    shutil.copytree(FIXTURE, target)
    manifest = target / "assistants" / "investment-assistant.yaml"
    text = manifest.read_text(encoding="utf-8")
    marker = "    house_name: { type: string }\n"
    assert marker in text
    manifest.write_text(
        text.replace(marker, marker + "    desk: { type: string, default: equities }\n", 1), encoding="utf-8"
    )
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


async def _profile(installation_id: str, document: dict[str, Any], email: str = "admin@varga.dev") -> None:
    """An active version of the person's `investor_profile`, as the memory page writes one."""
    from choregos_api.db.models import AssistantInstallation, MemoryProfile, User
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_memory import ensure_store, spaces_of

    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        installation = await session.get(AssistantInstallation, installation_id)
        assert installation is not None
        spaces = await spaces_of(session, installation, user.id)
        assert spaces is not None
        store = await ensure_store(session, spaces, spaces.decl("investor_profile"), None)
        session.add(
            MemoryProfile(
                org_id=store.org_id, memory_store_id=store.id, version=1, document=document, status="active"
            )
        )


def _system(body: dict[str, Any]) -> str:
    """The instructions of the first message, without the citation rule a knowledge base adds after them."""
    first = body["messages"][0]
    assert first["role"] == "system"
    assert "{{" not in str(first["content"]) and "{%" not in str(first["content"])
    return str(first["content"]).partition("\n\n")[0]


async def _say(client: AsyncClient, conversation: str, text: str) -> dict[str, Any]:
    said = await client.post(f"/api/v1/me/conversations/{conversation}/messages", json={"input": text})
    assert said.status_code == 200 and said.json()["status"] == "completed", said.text
    return dict(said.json())


async def test_the_first_model_request_carries_the_config_and_the_preferences_never_the_braces(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    # Publishing a version whose template reads `preferences` is accepted (SAMPLE_VARIABLES).
    await _agent(client, instructions=TEMPLATE)
    installation = await _assistant(client)
    await _profile(installation, {"risk_tolerance": "low", "horizon_years": 7})
    conversation = await _open(installation)

    upstream.model.say("Noted.")
    await _say(client, conversation, "Hello")

    (_, body) = upstream.model.requests[0]
    system = _system(body)
    assert system == (
        "You serve Varga from the equities desk. The person's risk tolerance is low. Horizon: 7 years."
    )


async def test_a_person_without_a_profile_reads_empty_preferences_not_a_sample(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    await _agent(client, instructions=TEMPLATE)
    installation = await _assistant(client)
    conversation = await _open(installation)

    upstream.model.say("Noted.")
    await _say(client, conversation, "Hello")

    system = _system(upstream.model.requests[0][1])
    assert system == "You serve Varga from the equities desk. The person's risk tolerance is ."
    assert "<risk_tolerance>" not in system, "the publication sample never reaches the model"


async def test_a_policy_without_memory_renders_no_preferences(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    await _agent(client, instructions=TEMPLATE)
    installation = await _assistant(client)
    await _profile(installation, {"risk_tolerance": "high", "horizon_years": 2})
    first = await _open(installation)
    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, first)
        assert record is not None
        space = record.project_id
    policy = (
        "apiVersion: choregos/v1\nkind: Policy\nmetadata: { name: no-memory, version: 2 }\n"
        "memory: { enabled: false }\n"
    )
    put = await client.put(f"/api/v1/projects/{space}/policy", json={"yaml": policy})
    assert put.status_code == 200, put.text

    conversation = await _open(installation)
    upstream.model.say("Noted.")
    await _say(client, conversation, "Hello")
    system = _system(upstream.model.requests[0][1])
    assert system == "You serve Varga from the equities desk. The person's risk tolerance is .", system


async def test_a_resumed_conversation_takes_the_installation_config_as_it_is_now(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _agent(client, instructions=TEMPLATE)
    installation = await _assistant(client)
    conversation = await _open(installation)
    upstream.model.say("Noted.").say("Again.").say("Still.")
    await _say(client, conversation, "Hello")
    assert _system(upstream.model.requests[0][1]).startswith("You serve Varga from")

    # Resumed with nothing changed: the same instructions, nothing journaled.
    soon = utcnow() + timedelta(hours=5)  # past the default idle window of four hours
    monkeypatch.setattr("choregos_api.services.conversations.utcnow", lambda: soon)
    await _say(client, conversation, "Back")
    assert _system(upstream.model.requests[-1][1]).startswith("You serve Varga from")
    assert "conversation.instructions_rendered" not in await _events(conversation)

    changed = await client.patch(
        f"{ORG}/assistants/investment-assistant", json={"config": {"house_name": "Varga Capital"}}
    )
    assert changed.status_code == 200, changed.text
    later = utcnow() + timedelta(hours=10)  # idle again
    monkeypatch.setattr("choregos_api.services.conversations.utcnow", lambda: later)
    await _say(client, conversation, "Still there?")

    system = _system(upstream.model.requests[-1][1])
    assert system.startswith("You serve Varga Capital from the equities desk."), system
    assert "conversation.instructions_rendered" in await _events(conversation)


async def test_a_plugin_agent_converses_without_the_work_item_context_of_a_run(
    client: AsyncClient, admin: str, upstream: Upstream, offline: Path
) -> None:
    """The plugin's own agent carries the work item's context of a run before its instructions; a
    conversation has no work item: the block is left out, and its braces never reach the model."""
    installation = await _assistant(client)
    conversation = await _open(installation, agent="investment-analyst")

    upstream.model.say("Noted.")
    await _say(client, conversation, "Hello")

    first = str(upstream.model.requests[0][1]["messages"][0]["content"])
    assert first.startswith("You help the people of Varga understand markets"), first[:200]
    assert "## Work item" not in first and "{{" not in first and "{%" not in first
