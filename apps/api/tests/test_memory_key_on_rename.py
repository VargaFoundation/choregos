"""A slug change must not orphan an organisation's global memories (S40-24).

Global memories live under the namespace `org:<key>#memory:<store>` (and the Ecphoria tenant
`org:<key>`). The slug is in every URL and may be renamed, so the key is the slug the organisation had
when its slug first changed, pinned in `settings["memory_key"]`. A memory backend that keys facts by
namespace (the fake, Ecphoria) would otherwise lose them all.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from choregos_adapters.fakes.memory import FakeMemory
from choregos_core.domain import Fact
from choregos_core.memory_scope import memory_key
from httpx import AsyncClient

from .agent_scopes_kit import ORG
from .test_renommer_une_organisation import _declarer_administrateur

ORGS = "/api/v1/orgs"


@pytest.fixture
def memory(monkeypatch: pytest.MonkeyPatch) -> FakeMemory:
    """One in-memory backend shared by every request, keyed by namespace like Ecphoria."""
    shared = FakeMemory()
    monkeypatch.setattr("choregos_adapters.build", lambda kind, type_name, config=None: shared)
    return shared


@pytest.fixture
async def platform_admin(admin: str) -> Any:
    from choregos_api.edition import reinitialiser

    await _declarer_administrateur({"admin@varga.dev"})
    yield
    reinitialiser()


def test_the_key_is_the_slug_until_a_rename_pins_it() -> None:
    assert memory_key(SimpleNamespace(slug="varga", settings=None)) == "varga"
    assert memory_key(SimpleNamespace(slug="varga", settings={})) == "varga"
    assert memory_key(SimpleNamespace(slug="diametral", settings={"memory_key": "varga"})) == "varga"
    assert memory_key(SimpleNamespace(slug="x", settings={"memory_key": ""})) == "x"


async def _seed_and_search(client: AsyncClient, memory: FakeMemory, org: str) -> list[str]:
    found = await client.get(f"{ORGS}/{org}/memories/lessons/search", params={"q": "TLS"})
    assert found.status_code == 200, found.text
    return [m["content"] for m in found.json()]


async def test_a_global_memory_survives_a_rename_and_a_second_one(
    client: AsyncClient, admin: str, platform_admin: None, memory: FakeMemory
) -> None:
    assert (
        await client.post(f"{ORG}/memories", json={"slug": "lessons", "display_name": "L"})
    ).status_code == 201
    await memory.write_fact(
        "org:varga#memory:lessons", Fact(kind="decision", subject="tls", content="Pin TLS 1.2.")
    )
    assert await _seed_and_search(client, memory, "varga") == ["Pin TLS 1.2."]

    renamed = await client.patch(f"{ORGS}/varga", json={"slug": "diametral", "name": "Diametral"})
    assert renamed.status_code == 200, renamed.text
    assert await _seed_and_search(client, memory, "diametral") == ["Pin TLS 1.2."], "the memory followed"

    again = await client.patch(f"{ORGS}/diametral", json={"slug": "dia"})
    assert again.status_code == 200, again.text
    assert await _seed_and_search(client, memory, "dia") == ["Pin TLS 1.2."]
    assert set(memory.facts) == {"org:varga#memory:lessons"}, "nothing was ever written under another key"

    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(Organization).where(Organization.slug == "dia"))).scalar_one()
        assert row.settings["memory_key"] == "varga", "the SECOND rename kept the FIRST key"


async def test_an_organisation_never_renamed_uses_its_slug(
    client: AsyncClient, admin: str, platform_admin: None, memory: FakeMemory
) -> None:
    await client.post(f"{ORG}/memories", json={"slug": "lessons", "display_name": "L"})
    await memory.write_fact(
        "org:varga#memory:lessons", Fact(kind="decision", subject="tls", content="Pin TLS.")
    )
    # A name or currency change is not a slug change: no key is pinned.
    assert (await client.patch(f"{ORGS}/varga", json={"name": "Varga", "currency": "USD"})).status_code == 200
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        assert "memory_key" not in (row.settings or {})
    assert await _seed_and_search(client, memory, "varga") == ["Pin TLS."]


async def test_an_old_slug_kept_as_a_memory_key_is_not_free(
    client: AsyncClient, admin: str, platform_admin: None, memory: FakeMemory
) -> None:
    """Else a new organisation named `varga` would read the renamed one's memories."""
    assert (await client.patch(f"{ORGS}/varga", json={"slug": "diametral"})).status_code == 200
    taken = await client.post(ORGS, json={"slug": "varga", "name": "Another"})
    assert taken.status_code == 409, taken.text
