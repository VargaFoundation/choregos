"""M6 — the HR scenario end to end (S20-07), on fakes, in accelerated time.

Joiner: an HR person's Claude files the request through the MCP door; the registry agent prepares
the access plan; an HR person validates it; at D-10 the account and its group are born in the
directory — the organisation's policy has decided; the sensitive group waits for a re-authenticated
person; at D-7 the laptop is ordered from the supplier's agent (MCP, under approval), enrolled in
the device fleet under the serial number the supplier assigned, and shipped; reception hands over
the badge — a task, attested — and the platform activates it; at D+1 it checks that everything holds.

Leaver: at D0 the account, its sessions and the badge are cut in one gesture; the laptop is taken
back, received — a task — and wiped; the platform checks.

Each building block is proven on its fake: removing one turns the scenario red.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus
from httpx import AsyncClient

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

ORG = "/api/v1/orgs/varga"
KEY_DIRECTORY = "secret-de-l-application-entra"
KEY_FLEET = "cle-du-parc"
KEY_SUPPLIER = "jeton-du-fournisseur"
ATTESTATION_BADGE = "I handed the badge to its holder in person"
ATTESTATION_RECEPTION = "I received this laptop and checked its serial number"


class Fakes:
    """The systems the HR scenario touches, each with the key it expects."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import choregos_adapters
        from choregos_adapters.fakes.entra import FakeEntra
        from choregos_adapters.fakes.rh import (
            FakeBadges,
            FakeFournisseur,
            FakeMdm,
            FakeTransporteur,
            serveur_mcp,
        )

        self.directory = FakeEntra(secret_attendu=KEY_DIRECTORY)
        self.fleet = FakeMdm(cle_attendue=KEY_FLEET)
        self.carrier = FakeTransporteur(cle_attendue=KEY_FLEET)
        self.readers = FakeBadges(cle_attendue=KEY_FLEET)
        self.supplier = FakeFournisseur()
        self.server = serveur_mcp(self.supplier, jeton=KEY_SUPPLIER)
        monkeypatch.setattr(choregos_adapters, "FAUX_ENTRA", self.directory)
        monkeypatch.setattr(choregos_adapters, "FAKE_MCP", self.server)
        for famille, fake in (
            ("mdm", self.fleet),
            ("shipping", self.carrier),
            ("access_control", self.readers),
        ):
            monkeypatch.setitem(choregos_adapters.FAUX_METIER, famille, fake)
        monkeypatch.setenv("CONNECTOR_E2E_KEY_DIRECTORY", KEY_DIRECTORY)
        monkeypatch.setenv("CONNECTOR_E2E_KEY_FLEET", KEY_FLEET)
        monkeypatch.setenv("CONNECTOR_E2E_KEY_SUPPLIER", KEY_SUPPLIER)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fakes:
    return Fakes(monkeypatch)


async def _organisation(client: AsyncClient) -> None:
    """The administrator's gesture: the five connectors the template announces, their keys by
    reference, and what the organisation allows without a human decision."""
    for body in (
        {"name": "directory", "kind": "identity", "type": "entra",
         "config": {"tenant_id": "acme", "client_id": "choregos"},
         "secret_refs": {"client_secret": "env:CONNECTOR_E2E_KEY_DIRECTORY"}},
        {"name": "devices", "kind": "mdm", "type": "demo",
         "secret_refs": {"api_key": "env:CONNECTOR_E2E_KEY_FLEET"}},
        {"name": "carrier", "kind": "shipping", "type": "demo",
         "secret_refs": {"api_key": "env:CONNECTOR_E2E_KEY_FLEET"}},
        {"name": "badge-readers", "kind": "access_control", "type": "demo",
         "secret_refs": {"api_key": "env:CONNECTOR_E2E_KEY_FLEET"}},
        {"name": "supplier", "kind": "mcp", "type": "mcp", "config": {"url": "https://supplier.test/mcp"},
         "secret_refs": {"token": "env:CONNECTOR_E2E_KEY_SUPPLIER"}},
    ):  # fmt: skip
        cree = await client.post(f"{ORG}/connectors", json=body)
        assert cree.status_code == 201, cree.text
    # The supplier's tools are DISCOVERED, and are born closed.
    discovery = await client.post(f"{ORG}/connectors/supplier/discover")
    assert discovery.status_code == 200, discovery.text
    operations = {
        o["name"]: o["policy"] for o in (await client.get(f"{ORG}/connectors/supplier")).json()["operations"]
    }
    assert operations == {"order_laptop": "forbidden", "order_status": "forbidden"}, (
        "a discovered tool is born closed"
    )
    permises = {
        "directory": ["create_user", "add_to_group", "remove_from_group", "disable_user", "revoke_sessions"],
        "devices": ["enroll_device", "wipe_device"],
        "carrier": ["create_shipment", "create_return"],
        "badge-readers": ["activate_badge", "deactivate_badge"],
    }
    for connector, names in permises.items():
        for name in names:
            ouverte = await client.patch(
                f"{ORG}/connectors/{connector}/operations/{name}", json={"policy": "allowed"}
            )
            assert ouverte.status_code == 200, ouverte.text
    # A purchase is decided: the laptop order stays under approval.
    sous_validation = await client.patch(
        f"{ORG}/connectors/supplier/operations/order_laptop", json={"policy": "approval"}
    )
    assert sous_validation.status_code == 200, sous_validation.text


async def _hr_project(client: AsyncClient) -> dict[str, Any]:
    cree = await client.post(
        f"{ORG}/projects",
        json={
            "slug": "rh",
            "name": "RH",
            "template_ref": "joiners-leavers",
            "config": {"slug": "rh", "org": "varga"},
        },
    )
    assert cree.status_code == 201, cree.text
    project = dict(cree.json())
    tracker = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    return project


async def _par_claude(platform: Platform, client: AsyncClient, fields: dict[str, Any]) -> dict[str, Any]:
    """The request, filed by an HR person's Claude: an `mcp:write` token, the project's door."""
    token = (
        await client.post("/api/v1/me/tokens", json={"name": "claude-de-la-rh", "scopes": ["mcp:write"]})
    ).json()
    claude = AsyncClient(transport=client._transport, base_url="http://e2e")
    try:
        call = await claude.post(
            "/mcp/projects/varga:rh",
            headers={"Authorization": f"Bearer {token['token']}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "create_work_item",
                             "arguments": {"title": f"Arrival of {fields['full_name']}",
                                           "workflow": "onboarding", "fields": fields}}},
        )  # fmt: skip
    finally:
        await claude.aclose()
    assert call.status_code == 200, call.text
    result = call.json()["result"]
    assert result["isError"] is False, result
    return dict(result["structuredContent"])


async def _start(platform: Platform, project: dict[str, Any], ticket: dict[str, Any]) -> Any:
    """The scenario starts the interpreter on the workers' queue, under the API's identifier."""
    from choregos_api.temporal import interpreter_id
    from choregos_core.domain import WorkItemData

    platform.adapters.tracker.items.setdefault(
        ticket["key"], WorkItemData(key=ticket["key"], title="RH", state="Todo")
    )
    return await platform.env.client.start_workflow(
        "WorkflowInterpreter",
        {"project_id": project["id"], "project_slug": "rh", "work_item_id": ticket["work_item"],
         "tracker_key": ticket["key"]},
        id=interpreter_id("rh", ticket["key"]),
        task_queue="e2e",
    )  # fmt: skip


async def _state(client: AsyncClient, ticket_id: str, expected: str, quoi: str) -> dict[str, Any]:
    for _ in range(400):
        lu = (await client.get(f"/api/v1/work-items/{ticket_id}")).json()
        if lu["state"] == expected:
            return dict(lu)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{quoi}: the ticket is in `{lu['state']}`, not in `{expected}`")


async def _request(client: AsyncClient, ticket_id: str) -> dict[str, Any]:
    """The human request the interpreter has just opened on the ticket."""
    for _ in range(400):
        lu = (await client.get(f"/api/v1/work-items/{ticket_id}")).json()
        if lu.get("pending_request"):
            return dict(lu["pending_request"])
        await asyncio.sleep(0.05)
    raise AssertionError("no human request open")


async def _wait_for(handle: Any, predicat: Any, quoi: str) -> dict[str, Any]:
    for _ in range(400):
        status = await handle.query("status")
        if predicat(status):
            return dict(status)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{quoi}: never true ({await handle.query('status')})")


async def _approuver_l_action(client: AsyncClient, project: dict[str, Any], genre: str) -> dict[str, Any]:
    """The pending action of this kind, approved by a re-authenticated person."""
    for _ in range(400):
        waiting = (
            await client.get(
                f"/api/v1/projects/{project['id']}/actions", params={"status": "pending_approval"}
            )
        ).json()
        trouvees = [a for a in waiting if a["kind"] == genre]
        if trouvees:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"no `{genre}` action pending")
    decidee = await client.post(
        f"/api/v1/projects/{project['id']}/actions/{trouvees[0]['id']}/decision", json={"decision": "approve"}
    )
    assert decidee.status_code == 200 and decidee.json()["status"] == "approved", decidee.text
    return dict(decidee.json())


async def _actions(client: AsyncClient, project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["kind"]: a for a in (await client.get(f"/api/v1/projects/{project['id']}/actions")).json()}


def _plan(name: str) -> StageResult:
    return StageResult(
        status=StageStatus.DONE, summary="plan ready", outputs={name: f"## {name}\n- nothing unusual"}
    )


async def test_an_arrival_from_the_request_by_claude_to_the_check_on_d1(
    platform: Platform, worker: Any, fake: Fakes
) -> None:
    client = platform.client
    await login(client)
    await _organisation(client)
    project = await _hr_project(client)
    start = await platform.env.get_current_time()
    arrival = (start + timedelta(days=15)).date()
    fields = {
        "full_name": "Léa Martin",
        "upn": "lea@acme.test",
        "start_date": arrival.isoformat(),
        "job": "Developer",
        "group": "devs",
        "sensitive_group": "prod-read",
        "address": "12 Peace Street, Paris",
    }
    ticket = await _par_claude(platform, client, fields)

    async with worker():
        platform.adapters.executor.queue_result(_plan("access_plan"))
        interprete = await _start(platform, project, ticket)
        await _state(client, ticket["work_item"], "planned", "the registry agent prepared the plan")
        await _request(client, ticket["work_item"])
        assert (
            await client.post(f"/api/v1/work-items/{ticket['work_item']}/decisions", json={"kind": "approve"})
        ).status_code == 202

        # D-10: the policy allowed the account and its group — the action is born approved and plays.
        status = await _wait_for(interprete, lambda s: s.get("waiting_until"), "D-10 awaited")
        assert status["waiting_until"].startswith((arrival - timedelta(days=10)).isoformat())
        assert fake.directory.comptes == {}, "nothing before D-10"
        await platform.env.sleep(timedelta(days=5, hours=1))
        await _state(client, ticket["work_item"], "accounts_created", "the account is created at D-10")
        (account,) = fake.directory.comptes.values()
        assert (
            account["userPrincipalName"] == "lea@acme.test"
            and account["id"] in fake.directory.membres_de_l_unite
        )
        assert account["id"] in fake.directory.groupes["devs"]

        # The sensitive group: a re-authenticated person.
        await _approuver_l_action(client, project, "onboarding.sensitive_group")
        await _state(client, ticket["work_item"], "sensitive_granted", "the sensitive group is decided")
        assert account["id"] in fake.directory.groupes["prod-read"]

        # D-7: the supplier order is decided; the fleet and the shipment follow its serial number.
        await _wait_for(interprete, lambda s: s.get("waiting_until"), "D-7 awaited")
        assert fake.supplier.commandes == {}
        await platform.env.sleep(timedelta(days=3))
        await _approuver_l_action(client, project, "onboarding.laptop")
        await _state(
            client, ticket["work_item"], "laptop_shipped", "the laptop is ordered, enrolled, shipped"
        )
        (order,) = fake.supplier.commandes.values()
        serial = order["serial"]
        assert fake.fleet.postes[serial] == {"serial": serial, "upn": "lea@acme.test", "state": "enrolled"}
        (shipment,) = fake.carrier.envois.values()
        assert (shipment["items"], shipment["address"]) == ([serial], fields["address"])
        assert {e["authorization"] for _, e, _ in fake.server.recues} == {f"Bearer {KEY_SUPPLIER}"}

        # The badge: a task, its proof; then the platform activates it.
        request = await _request(client, ticket["work_item"])
        assert (request["kind"], request["payload"]["attest"]) == ("task", ATTESTATION_BADGE)
        task = await client.post(
            f"/api/v1/work-items/{ticket['work_item']}/decisions",
            json={"kind": "complete", "values": {"badge_uid": "04A1B2C3"}, "attested": True},
        )
        assert task.status_code == 202, task.text
        await _state(client, ticket["work_item"], "active", "the badge is active")
        assert fake.readers.badges["04A1B2C3"] == {
            "uid": "04A1B2C3",
            "holder": "lea@acme.test",
            "active": True,
        }

        # D+1: the platform re-reads and checks.
        await platform.env.sleep(timedelta(days=12))
        result = await interprete.result()
    assert result["state"] == "ready"

    actions = await _actions(client, project)
    assert {k: a["status"] for k, a in actions.items()} == {
        "onboarding.accounts": "succeeded",
        "onboarding.sensitive_group": "succeeded",
        "onboarding.laptop": "succeeded",
        "onboarding.badge": "succeeded",
        "onboarding.check": "succeeded",
    }
    assert actions["onboarding.accounts"]["decisions"][0]["by"] == "policy"
    assert actions["onboarding.sensitive_group"]["decisions"][0]["by"] == "admin@varga.dev"
    assert actions["onboarding.sensitive_group"]["decisions"][0]["auth_age_seconds"] is not None
    chronologie = (await client.get(f"/api/v1/work-items/{ticket['work_item']}/timeline")).text
    assert ATTESTATION_BADGE in chronologie, "the proof of the handover can be read in the case file"


async def test_a_departure_on_d0_cuts_everything_the_workstation_is_taken_back_received_wiped(
    platform: Platform, worker: Any, fake: Fakes
) -> None:
    client = platform.client
    await login(client)
    await _organisation(client)
    project = await _hr_project(client)
    identifiant = fake.directory.ajouter_compte("paul@acme.test")
    fake.directory.groupes["devs"] = {identifiant}
    fake.readers.badges["04D5E6F7"] = {"uid": "04D5E6F7", "holder": "paul@acme.test", "active": True}
    fake.fleet.postes["PC-0042"] = {"serial": "PC-0042", "upn": "paul@acme.test", "state": "enrolled"}
    start = await platform.env.get_current_time()
    depart = (start + timedelta(days=3)).date()
    fields = {"full_name": "Paul Durand", "upn": "paul@acme.test", "last_day": depart.isoformat(),
              "badge_uid": "04D5E6F7", "serial": "PC-0042", "address": "8 Fog Quay, Lyon"}  # fmt: skip
    cree = await client.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={"title": "Departure of Paul", "labels": ["leaver"], "fields": fields},
    )
    assert cree.status_code == 201 and cree.json()["workflow_name"] == "offboarding", cree.text
    ticket = {"work_item": cree.json()["id"], "key": cree.json()["tracker_key"]}

    async with worker():
        platform.adapters.executor.queue_result(_plan("leaver_plan"))
        interprete = await _start(platform, project, ticket)
        await _state(client, ticket["work_item"], "planned", "the leaver plan is ready")
        await _request(client, ticket["work_item"])
        assert (
            await client.post(f"/api/v1/work-items/{ticket['work_item']}/decisions", json={"kind": "approve"})
        ).status_code == 202
        await _wait_for(interprete, lambda s: s.get("waiting_until"), "D0 awaited")
        assert fake.directory.comptes[identifiant]["accountEnabled"] is True, "nothing before D0"
        await platform.env.sleep(timedelta(days=3, hours=1))
        await _state(
            client, ticket["work_item"], "pickup_scheduled", "the access is cut and the pickup scheduled"
        )
        assert fake.directory.comptes[identifiant]["accountEnabled"] is False
        assert fake.directory.sessions_revoquees == ["paul@acme.test"]
        assert fake.readers.badges["04D5E6F7"]["active"] is False
        retour = fake.carrier.envois[f"{ticket['key']}-return"]
        assert (retour["direction"], retour["address"]) == ("return", fields["address"])

        assert (await _request(client, ticket["work_item"]))["kind"] == "task"
        reception = await client.post(
            f"/api/v1/work-items/{ticket['work_item']}/decisions",
            json={"kind": "complete", "values": {"laptop_condition": "intact"}, "attested": True},
        )
        assert reception.status_code == 202, reception.text
        result = await interprete.result()
    assert result["state"] == "closed"
    assert fake.fleet.postes["PC-0042"]["state"] == "wiped"
    actions = await _actions(client, project)
    assert {k: a["status"] for k, a in actions.items()} == {
        "offboarding.cut_access": "succeeded",
        "offboarding.pickup": "succeeded",
        "offboarding.wipe": "succeeded",
        "offboarding.check": "succeeded",
    }
    chronologie = (await client.get(f"/api/v1/work-items/{ticket['work_item']}/timeline")).text
    assert ATTESTATION_RECEPTION in chronologie
