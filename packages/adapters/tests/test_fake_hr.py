"""The fakes of the HR scenario (S20-04): what the conformance suite does not say — what each one
REFUSES, by name, and what the staging moves forward. Idempotence, schemas and secrets:
`tests/conformance/connecteurs/`."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.errors import AdapterError, ConfigurationError
from choregos_adapters.fakes.hr import (
    FakeBadges,
    FakeCarrier,
    FakeMdm,
    FakeSupplier,
    mcp_server,
)


def test_a_device_enrolled_for_someone_else_is_refused_and_a_wiped_device_enrols_again() -> None:
    fleet = FakeMdm()
    fleet.perform("enroll_device", {"serial": "PC-1", "upn": "lea@acme.test"})
    with pytest.raises(AdapterError, match="inscrit pour un autre utilisateur"):  # lang: fr-ok (pinned)
        fleet.perform("enroll_device", {"serial": "PC-1", "upn": "paul@acme.test"})
    with pytest.raises(AdapterError, match="no device PC-2"):
        fleet.perform("wipe_device", {"serial": "PC-2"})
    assert fleet.perform("wipe_device", {"serial": "PC-1"})["upn"] is None, "wiped, the device is released"
    taken_over = fleet.perform("enroll_device", {"serial": "PC-1", "upn": "paul@acme.test"})
    assert (taken_over["upn"], taken_over["state"], taken_over["enrolled"]) == (
        "paul@acme.test",
        "enrolled",
        True,
    )


def test_a_reference_taken_by_another_shipment_is_refused_and_the_parcel_is_tracked() -> None:
    carrier = FakeCarrier()
    shipment = carrier.perform("create_shipment", {"reference": "HR-1", "address": "Lyon", "items": ["PC-1"]})
    assert shipment["state"] == "shipped" and shipment["tracking"].startswith("TRK-")
    with pytest.raises(AdapterError, match="already taken"):
        carrier.perform("create_shipment", {"reference": "HR-1", "address": "Paris"})
    with pytest.raises(AdapterError, match="already taken"):
        carrier.perform("create_return", {"reference": "HR-1", "address": "Lyon"})
    carrier.deliver("HR-1")
    assert carrier.perform("shipment_status", {"reference": "HR-1"})["state"] == "delivered"
    collection = carrier.perform("create_return", {"reference": "HR-1-return", "address": "Lyon"})
    assert collection["tracking"].startswith("RET-") and collection["state"] == "collection_booked"


def test_a_badge_active_for_someone_else_is_refused_and_an_unknown_badge_is_not_cut() -> None:
    badges = FakeBadges()
    badges.perform("activate_badge", {"uid": "04A1", "holder": "lea@acme.test"})
    with pytest.raises(AdapterError, match="active for another holder"):
        badges.perform("activate_badge", {"uid": "04A1", "holder": "paul@acme.test"})
    # A UID mistyped at departure: the refusal says that the real badge still opens.
    with pytest.raises(AdapterError, match="no badge 04B2"):
        badges.perform("deactivate_badge", {"uid": "04B2"})
    assert badges.perform("deactivate_badge", {"uid": "04A1"})["deactivated"] is True
    assert badges.perform("badge_status", {"uid": "04A1"})["active"] is False


def test_an_unknown_argument_or_operation_is_refused_by_name() -> None:
    with pytest.raises(AdapterError, match="enroll_device"):
        FakeMdm().perform("enroll_device", {"serial": "PC-1", "upn": "x", "colour": "blue"})
    with pytest.raises(AdapterError, match="no operation format_disk"):
        FakeMdm().perform("format_disk", {})


async def test_the_suppliers_agent_orders_tracks_and_refuses_over_mcp() -> None:
    from choregos_adapters.mcp import McpClient

    supplier = FakeSupplier()
    client = McpClient(
        "https://supplier.test/mcp",
        token="key",
        transport=mcp_server(supplier, token="key").transport(),
    )
    tools = {o.name: o for o in await client.list_tools()}
    assert {n: o.read_only for n, o in tools.items()} == {"order_laptop": False, "order_status": True}
    order = await client.call_tool("order_laptop", {"reference": "HR-1", "model": "p14", "upn": "lea"})
    serial = order["structuredContent"]["serial"]
    assert serial.startswith("PC-") and order["structuredContent"]["state"] == "confirmed"
    supplier.ship("HR-1")
    tracking: dict[str, Any] = (await client.call_tool("order_status", {"reference": "HR-1"}))[
        "structuredContent"
    ]
    assert (tracking["state"], tracking["serial"], tracking["tracking"]) == (
        "shipped",
        serial,
        f"COLIS-{serial[3:]}",
    )
    # A refusal by the supplier is a tool result in error, not a protocol error.
    refusal = await client.call_tool("order_laptop", {"reference": "HR-1", "model": "p16", "upn": "lea"})
    assert refusal["isError"] is True and "for another laptop" in refusal["content"][0]["text"]
    assert len(supplier.orders) == 1
    # S39-11: the names of the 1.x template are still answered (not announced): same order, same state.
    former = await client.call_tool(  # lang: fr-ok
        "commander_poste",
        {"reference": "HR-1", "modele": "p14", "upn": "lea"},  # lang: fr-ok
    )
    assert former["structuredContent"]["created"] is False and former["structuredContent"]["serial"] == serial
    older = await client.call_tool("suivi_commande", {"reference": "HR-1"})  # lang: fr-ok
    assert older["structuredContent"]["state"] == "shipped"


def test_the_families_are_connector_types_and_demo_is_refused_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_adapters import build, connector_types

    demos = {
        k: {o.name: o.default_policy for o in s.operations} for k, t, s in connector_types() if t == "demo"
    }
    # The demo `cd` (S21-24) exposes nothing: the train calls it, never an agent.
    assert demos.pop("cd") == {}
    families = demos
    assert families == {
        "mdm": {"enroll_device": "approval", "wipe_device": "approval", "device_status": "allowed"},
        "shipping": {
            "create_shipment": "approval",
            "create_return": "approval",
            "shipment_status": "allowed",
        },
        "access_control": {
            "activate_badge": "approval",
            "deactivate_badge": "approval",
            "badge_status": "allowed",
        },
    }
    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    monkeypatch.setenv("CHOREGOS_ENV", "prod")
    # The message is the registry's (S45-10 translates it).
    with pytest.raises(ConfigurationError, match="`mdm: demo` refusé en prod"):  # lang: fr-ok
        build("mdm", "demo", {})
    monkeypatch.setenv("CHOREGOS_ENV", "dev")
    assert type(build("mdm", "demo", {}).fake).__name__ == "FakeMdm"
