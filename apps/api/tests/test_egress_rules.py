"""An organisation's egress rules, requests and decisions (S39-22a, ADR 0066)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .egress_support import ORG, add_member, as_person, audit_actions, rules_in_db


@pytest.fixture
def enforce(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()


async def _create(client: AsyncClient, host: str = "mcp.example.test", **extra: Any) -> Any:
    return await client.post(f"{ORG}/egress/rules", json={"host": host, **extra})


async def test_an_administrator_opens_a_host_and_it_is_audited(client: AsyncClient, admin: str) -> None:
    created = await _create(client, "MCP.Example.Test", used_by=["api", "workers"], reason="Notion")
    assert created.status_code == 201, created.text
    rule = created.json()
    assert (rule["host"], rule["ports"], rule["used_by"], rule["status"]) == (
        "mcp.example.test",
        [443],
        ["api", "workers"],
        "allowed",
    )
    assert rule["decided_by"] == admin and rule["reason"] == "Notion"
    assert "egress.rule.create" in await audit_actions()
    overview = (await client.get(f"{ORG}/egress")).json()
    assert [r["host"] for r in overview["rules"]] == ["mcp.example.test"] and overview["can_decide"]


async def test_a_developer_cannot_open_a_host_and_a_duplicate_is_refused(
    client: AsyncClient, admin: str
) -> None:
    assert (await _create(client)).status_code == 201
    assert (await _create(client)).status_code == 409, "one live rule per host"
    async with as_person(client, "dev@varga.dev", "developer") as dev:
        assert (await _create(dev, "other.example.test")).status_code == 403
        assert (await dev.get(f"{ORG}/egress")).status_code == 200, "members read the list"
    assert (await _create(client, "bad host")).status_code == 422


async def test_a_request_is_approved_or_refused_and_the_decision_is_remembered(
    client: AsyncClient, admin: str
) -> None:
    async with as_person(client, "dev@varga.dev", "developer") as dev:
        asked = await dev.post(
            f"{ORG}/egress/requests",
            json={"host": "mcp.example.test", "reason": "need it", "origin": "org-connector:x"},
        )
        assert asked.status_code == 201 and asked.json()["status"] == "requested", asked.text
        again = await dev.post(f"{ORG}/egress/requests", json={"host": "mcp.example.test"})
        assert again.json()["id"] == asked.json()["id"], "asking twice is the same request"
        assert (await dev.post(f"{ORG}/egress/requests/{asked.json()['id']}/approve")).status_code == 403
        other = (await dev.post(f"{ORG}/egress/requests", json={"host": "b.example.test"})).json()
    approved = await client.post(f"{ORG}/egress/requests/{asked.json()['id']}/approve", json={"note": "ok"})
    assert approved.status_code == 200, approved.text
    assert (approved.json()["status"], approved.json()["decided_by"]) == ("allowed", admin)
    refused = await client.post(f"{ORG}/egress/requests/{other['id']}/refuse", json={"note": "no"})
    assert (refused.json()["status"], refused.json()["decision_note"]) == ("refused", "no")
    assert (await client.post(f"{ORG}/egress/requests/{other['id']}/approve")).status_code == 409
    overview = (await client.get(f"{ORG}/egress")).json()
    assert overview["requests"] == []
    assert await audit_actions() == [
        "egress.request.create",
        "egress.request.create",
        "egress.request.approve",
        "egress.request.refuse",
    ]


async def test_a_host_already_allowed_cannot_be_requested_and_requests_are_capped(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.services.egress import MAX_OPEN_REQUESTS

    assert (await _create(client)).status_code == 201
    async with as_person(client, "dev@varga.dev", "developer") as dev:
        assert (
            await dev.post(f"{ORG}/egress/requests", json={"host": "mcp.example.test"})
        ).status_code == 409
        for n in range(MAX_OPEN_REQUESTS):
            assert (
                await dev.post(f"{ORG}/egress/requests", json={"host": f"h{n}.example.test"})
            ).status_code == 201
        capped = await dev.post(f"{ORG}/egress/requests", json={"host": "one-more.example.test"})
        assert capped.status_code == 429


async def test_a_revoked_host_is_no_longer_allowed_by_the_policy(
    client: AsyncClient, admin: str, enforce: None
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.egress import load_policy
    from sqlalchemy import select

    rule = (await _create(client)).json()

    async def verdict() -> Any:
        async with session_scope() as session:
            organisation = (await session.execute(select(Organization))).scalar_one()
            return (await load_policy(session, organisation)).verdict("mcp.example.test")

    assert (await verdict()).ok
    revoked = await client.delete(f"{ORG}/egress/rules/{rule['id']}")
    assert revoked.json()["status"] == "revoked", revoked.text
    assert (await verdict()).org == "absent"
    assert (await client.delete(f"{ORG}/egress/rules/{rule['id']}")).status_code == 409
    assert "egress.rule.revoke" in await audit_actions()
    assert (await _create(client)).status_code == 201, "a revoked host can be opened again"
    assert [r.status for r in await rules_in_db()] == ["revoked", "allowed"]


async def test_check_derives_the_hosts_and_reports_both_layers(
    client: AsyncClient, admin: str, enforce: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_CEILING", '{"api": ["api.github.com"]}')
    reset_settings_cache()
    await add_member("root@varga.dev", "org_admin")
    assert (await _create(client)).status_code == 201
    checked = await client.post(
        f"{ORG}/egress/check",
        json={
            "origin": {
                "kind": "org-connector",
                "type": "mcp",
                "config": {"url": "https://mcp.example.test/mcp"},
            }
        },
    )
    assert checked.status_code == 200, checked.text
    body = checked.json()
    (verdict,) = body["verdicts"]
    assert (verdict["host"], verdict["org"], verdict["ceiling"]) == ("mcp.example.test", "allowed", "outside")
    assert verdict["can_allow"] and body["ok"] is False
    change = verdict["platform_change"]
    assert change["needed"] and "mcp.example.test" in change["snippet"], "the snippet names the host"
    assert any(a["email"] == "root@varga.dev" for a in verdict["approvers"])
    assert (await client.post(f"{ORG}/egress/check", json={})).status_code == 422
