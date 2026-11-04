"""Business families in the organisation (S20-04): a fleet, a carrier, badge readers are declared
like any connector (the key by reference), their operations are born with their policy, and an
approved write reaches the system with the key resolved by the platform.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
KEY = "fleet-demo-key"


@pytest.fixture
def fleet(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.hr import FakeMdm

    fake_mdm = FakeMdm(expected_key=KEY)
    monkeypatch.setitem(choregos_adapters.FAUX_METIER, "mdm", fake_mdm)
    monkeypatch.setenv("CONNECTOR_TEST_KEY_FLEET", KEY)
    yield fake_mdm


async def _declare(client: AsyncClient, kind: str, name: str) -> dict[str, Any]:
    body = {
        "name": name,
        "kind": kind,
        "type": "demo",
        "secret_refs": {"api_key": "env:CONNECTOR_TEST_KEY_FLEET"},
    }
    created = await client.post(f"{ORG}/connectors", json=body)
    assert created.status_code == 201, created.text
    assert KEY not in created.text, "a reference, never the value"
    return {o["name"]: o["policy"] for o in created.json()["operations"]}


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("mdm", {"enroll_device": "approval", "wipe_device": "approval", "device_status": "allowed"}),
        (
            "shipping",
            {"create_shipment": "approval", "create_return": "approval", "shipment_status": "allowed"},
        ),
        (
            "access_control",
            {"activate_badge": "approval", "deactivate_badge": "approval", "badge_status": "allowed"},
        ),
    ],
)
async def test_a_family_declares_its_key_by_reference_and_its_writes_go_through_a_validation(
    client: AsyncClient, admin: str, kind: str, expected: dict[str, str]
) -> None:
    assert await _declare(client, kind, f"{kind.replace('_', '-')}-demo") == expected
    in_clear = await client.post(
        f"{ORG}/connectors", json={"name": "other", "kind": kind, "type": "demo", "config": {"api_key": KEY}}
    )
    assert in_clear.status_code == 422 and "in clear" in in_clear.text


async def test_an_approved_write_reaches_the_fleet_with_the_resolved_key_and_a_refusal_is_final(
    client: AsyncClient, project: dict[str, Any], fleet: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effects import EffectContext, EffectRefused, effect

    await _declare(client, "mdm", "fleet")
    call = effect("connector.call")

    def enroll(upn: str) -> dict[str, Any]:
        return {
            "connector": "fleet",
            "operation": "enroll_device",
            "arguments": {"serial": "PC-1", "upn": upn},
        }

    async with session_scope() as session:
        project_row = await session.get(Project, project["id"])
        assert project_row is not None
        action = Action(
            org_id=project_row.org_id,
            project_id=project_row.id,
            origin="transition",
            kind="arrival.device",
            title="Lea's device",
            params={},
            effects=[],
            proposed_by={"kind": "system", "id": "system"},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        ctx = EffectContext(session, action, project_row)
        done = await call(ctx, enroll("lea@acme.test"))
        assert done["enrolled"] is True and KEY not in str(done)
        assert fleet.devices["PC-1"]["upn"] == "lea@acme.test"
        # The device is Lea's: enrolling it for Paul is a refusal, a final one: Temporal does not retry.
        # The fake's French messages are pinned (a former spelling): these two patterns stay French.
        with pytest.raises(EffectRefused, match="inscrit pour un autre utilisateur"):  # lang: fr-ok
            await call(ctx, enroll("paul@acme.test"))
        monkeypatch.setenv("CONNECTOR_TEST_KEY_FLEET", "a-revoked-key")
        with pytest.raises(EffectRefused, match="clé refusée"):  # lang: fr-ok
            await call(ctx, enroll("lea@acme.test"))
    assert fleet.devices["PC-1"]["upn"] == "lea@acme.test"
