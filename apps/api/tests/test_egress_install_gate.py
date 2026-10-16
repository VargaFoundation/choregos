"""Every governed "add" opens its host first: 409 `egress_required`, or, with the right, in the same
transaction (S39-22a, ADR 0066)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .egress_support import ORG, add_member, as_person, audit_actions, rules_in_db
from .test_skills_from_git import ANTHROPICS, REPO_V1, SHA1, tarball

VENDOR = {"name": "vendor", "type": "mcp", "config": {"url": "https://mcp.example.test/mcp"}}


@pytest.fixture
def fake() -> Iterator[Any]:
    """A recorded repository: no test reaches GitHub."""
    from choregos_api.services.skill_sources import FakeSourceFetcher, set_fetcher

    fetcher = FakeSourceFetcher()
    fetcher.add(ANTHROPICS, SHA1, tarball(REPO_V1), refs={"v1": SHA1})
    set_fetcher(fetcher)
    yield fetcher
    set_fetcher(None)


@pytest.fixture
def enforce(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()


async def test_without_enforcement_nothing_is_gated(client: AsyncClient, admin: str) -> None:
    """Under the fakes the mode is `warn`: the existing suites need no rule."""
    assert (await client.post(f"{ORG}/connectors", json=VENDOR)).status_code == 201
    assert await rules_in_db() == []


async def test_a_connector_whose_host_is_absent_is_a_409_with_the_verdicts(
    client: AsyncClient, admin: str, enforce: None
) -> None:
    await add_member("root@varga.dev", "org_admin")
    refused = await client.post(f"{ORG}/connectors", json=VENDOR)
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "egress_required" and body["status"] == 409
    (verdict,) = body["verdicts"]
    assert (verdict["host"], verdict["port"], verdict["org"], verdict["can_allow"]) == (
        "mcp.example.test",
        443,
        "absent",
        True,
    )
    assert verdict["used_by"] == "api"
    assert any(a["email"] == "root@varga.dev" for a in verdict["approvers"])
    assert (await client.get(f"{ORG}/connectors/vendor")).status_code == 404, "nothing was created"
    assert await rules_in_db() == []


async def test_allow_egress_opens_the_host_and_creates_the_connector_in_one_transaction(
    client: AsyncClient, admin: str, enforce: None
) -> None:
    created = await client.post(f"{ORG}/connectors", json={**VENDOR, "allow_egress": True})
    assert created.status_code == 201, created.text
    (rule,) = await rules_in_db("allowed")
    assert (rule.host, rule.origin, rule.decided_by, rule.used_by) == (
        "mcp.example.test",
        "org-connector:vendor",
        admin,
        ["api"],
    )
    assert "egress.rule.create" in await audit_actions()
    # the host is open now: the same add needs no flag
    again = await client.post(f"{ORG}/connectors", json={**VENDOR, "name": "vendor2"})
    assert again.status_code == 201, again.text
    # a failure after the gate takes the rule with it
    clash = await client.post(
        f"{ORG}/connectors",
        json={**VENDOR, "config": {"url": "https://other.example.test/mcp"}, "allow_egress": True},
    )
    assert clash.status_code == 409 and clash.json().get("code") != "egress_required"
    assert [r.host for r in await rules_in_db()] == ["mcp.example.test"], "no orphan rule"


async def test_editing_the_url_of_an_mcp_connector_is_gated_too(
    client: AsyncClient, admin: str, enforce: None
) -> None:
    assert (await client.post(f"{ORG}/connectors", json={**VENDOR, "allow_egress": True})).status_code == 201
    moved = await client.patch(
        f"{ORG}/connectors/vendor", json={"config": {"url": "https://moved.example.test/mcp"}}
    )
    assert moved.status_code == 409 and moved.json()["verdicts"][0]["host"] == "moved.example.test"
    ok = await client.patch(
        f"{ORG}/connectors/vendor",
        json={"config": {"url": "https://moved.example.test/mcp"}, "allow_egress": True},
    )
    assert ok.status_code == 200, ok.text


async def _import(client: AsyncClient, **extra: Any) -> Any:
    return await client.post(
        f"{ORG}/skills/import-git",
        json={"repo_url": ANTHROPICS, "sha": SHA1, "path": "skills", "skills": ["onboarding"], **extra},
    )


async def test_a_git_skill_import_by_a_project_owner_asks_an_administrator(
    client: AsyncClient, admin: str, fake: Any, enforce: None
) -> None:
    await add_member("root@varga.dev", "org_admin")
    async with as_person(client, "owner@varga.dev", "project_owner") as owner:
        asked = await _import(owner)
        assert asked.status_code == 409, asked.text
        verdicts = asked.json()["verdicts"]
        assert {v["host"] for v in verdicts} == {"api.github.com", "codeload.github.com"}
        assert all(not v["can_allow"] for v in verdicts)
        assert any(a["email"] == "root@varga.dev" for a in verdicts[0]["approvers"])
        forced = await _import(owner, allow_egress=True)
        assert forced.status_code == 403 and "root@varga.dev" in forced.text, "names who can"
    assert await rules_in_db() == []
    # the administrator opens them with the same call
    done = await _import(client, allow_egress=True)
    assert done.status_code == 201, done.text
    assert {r.host for r in await rules_in_db("allowed")} == {"api.github.com", "codeload.github.com"}
    assert {r.origin for r in await rules_in_db()} == {"skill-import:github.com/anthropics/skills"}
    # once open, the project owner imports without a flag
    async with as_person(client, "owner@varga.dev") as owner:
        assert (await _import(owner, skills=["pdf"])).status_code == 201


async def test_a_web_knowledge_source_is_gated(client: AsyncClient, admin: str, enforce: None) -> None:
    base = "/api/v1/orgs/varga/knowledge"
    assert (
        await client.post(base, json={"slug": "docs", "name": "Docs", "serving_mode": "index"})
    ).status_code == 201
    source = {"kind": "web", "name": "Site", "config": {"url": "https://docs.example.test/guide/"}}
    asked = await client.post(f"{base}/docs/sources", json=source)
    assert asked.status_code == 409, asked.text
    assert {(v["host"], v["used_by"]) for v in asked.json()["verdicts"]} == {
        ("docs.example.test", "api"),
        ("docs.example.test", "workers"),
    }
    done = await client.post(f"{base}/docs/sources", json={**source, "allow_egress": True})
    assert done.status_code == 201, done.text
    (rule,) = await rules_in_db("allowed")
    assert rule.used_by == ["api", "workers"] and rule.origin == "knowledge-source:Site"


async def test_the_ceiling_is_reported_and_the_platform_change_is_rendered(
    client: AsyncClient, admin: str, enforce: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_CEILING", '{"api": ["api.github.com"]}')
    monkeypatch.setenv(
        "CHOREGOS_EGRESS_PLATFORM_CHANGE",
        '{"format": "text", "owner": "the platform team", "entries": {"api": "api-github"},'
        ' "template": "entry {{entry}} add:\\n{{#hosts}}  - {{.}}\\n{{/hosts}}for {{reason}}",'
        ' "issueUrl": "https://git.example.test/new?title={{hosts_csv}}&body={{snippet_urlencoded}}"}',
    )
    reset_settings_cache()
    refused = await client.post(f"{ORG}/connectors", json=VENDOR)
    (verdict,) = refused.json()["verdicts"]
    assert verdict["ceiling"] == "outside"
    change = verdict["platform_change"]
    assert change["needed"] and change["owner"] == "the platform team"
    assert change["snippet"].startswith("entry api-github add:\n  - mcp.example.test\nfor ")
    assert change["issue_url"].startswith(
        "https://git.example.test/new?title=mcp.example.test&body=entry%20api-github"
    )
    # allowing it is still possible: the org list and the ceiling are two layers
    assert (await client.post(f"{ORG}/connectors", json={**VENDOR, "allow_egress": True})).status_code == 201
    checked = (
        await client.post(f"{ORG}/egress/check", json={"targets": [{"url": "https://mcp.example.test/x"}]})
    ).json()
    assert checked["ok"] is False and checked["verdicts"][0]["ceiling"] == "outside"
