"""The `entra` connector (Microsoft Graph, S20-03) against a fake Graph: every gesture replayable
without a double effect, the administrative unit held, `Retry-After` honoured."""

from __future__ import annotations

import pytest
from choregos_adapters.fakes.entra import FakeEntra
from choregos_adapters.identity import EntraIdentity, OutsideUnit


def _directory(graph: FakeEntra) -> EntraIdentity:
    return EntraIdentity(
        tenant_id="acme",
        client_id="choregos",
        client_secret="secret",
        administrative_unit_id=graph.unit,
        transport=graph.transport(),
    )


async def test_a_double_creation_gives_a_single_account_in_the_unit() -> None:
    graph = FakeEntra()
    directory = _directory(graph)
    first = await directory.create_user("lea.martin@acme.test", "Léa Martin")
    second = await directory.create_user("lea.martin@acme.test", "Léa Martin")
    assert first["created"] is True and second["created"] is False
    assert first["id"] == second["id"]
    assert len(graph.accounts) == 1
    assert first["id"] in graph.unit_members, "a created account enters the unit"


async def test_adding_twice_to_the_same_group_is_a_success() -> None:
    graph = FakeEntra()
    directory = _directory(graph)
    await directory.create_user("lea.martin@acme.test", "Léa Martin")
    assert (await directory.add_to_group("lea.martin@acme.test", "devs"))["added"] is True
    assert (await directory.add_to_group("lea.martin@acme.test", "devs"))["added"] is False
    assert (await directory.remove_from_group("lea.martin@acme.test", "devs"))["removed"] is True
    assert (await directory.remove_from_group("lea.martin@acme.test", "devs"))["removed"] is False


async def test_an_account_outside_the_unit_is_refused_by_name() -> None:
    graph = FakeEntra()
    graph.add_account("ceo@acme.test", in_unit=False)
    directory = _directory(graph)
    with pytest.raises(OutsideUnit, match="administrative unit"):
        await directory.disable_user("ceo@acme.test")
    with pytest.raises(OutsideUnit):
        await directory.revoke_sessions("ceo@acme.test")
    assert graph.accounts and all(a["accountEnabled"] for a in graph.accounts.values()), "nothing moved"


async def test_a_departure_disables_and_revokes() -> None:
    graph = FakeEntra()
    directory = _directory(graph)
    await directory.execute("create_user", {"upn": "paul@acme.test", "display_name": "Paul"})
    await directory.execute("disable_user", {"upn": "paul@acme.test"})
    await directory.execute("revoke_sessions", {"upn": "paul@acme.test"})
    (account,) = graph.accounts.values()
    assert account["accountEnabled"] is False
    assert graph.revoked_sessions == ["paul@acme.test"]
    assert (await directory.execute("get_user", {"upn": "absent@acme.test"})) == {"user": None}


async def test_a_429_is_waited_out_then_retried() -> None:
    graph = FakeEntra(throttled_requests=2)
    directory = _directory(graph)
    assert (await directory.create_user("lea@acme.test", "Léa"))["created"] is True
    assert sum(1 for m, c in graph.received if c.endswith("/users/lea@acme.test")) >= 3, (
        "two 429s, then the answer"
    )
