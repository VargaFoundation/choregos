"""The `joiners-leavers` template (S20-07): an HR project is born with its two workflows, their default
and their routing, and the organisation receives its two agents and its two skills, in version 1 when
they are not there, never rewritten when they are. The core effects its workflows call:
`connector.call`, of which an MCP server returns what it structured, and `check`, a control.
"""

from __future__ import annotations

import pathlib
import shutil
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
TEMPLATE = pathlib.Path(__file__).resolve().parents[3] / "templates" / "joiners-leavers"


async def _project(
    client: AsyncClient, slug: str = "hr", template: str = "joiners-leavers"
) -> dict[str, Any]:
    created = await client.post(
        f"{ORG}/projects",
        json={"slug": slug, "name": slug, "template_ref": template, "config": {"slug": slug, "org": "varga"}},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def test_an_hr_project_is_born_with_its_workflows_and_the_organisation_receives_its_agents_and_skills(
    client: AsyncClient, admin: str
) -> None:
    pid = (await _project(client))["id"]
    assert {w["name"] for w in (await client.get(f"/api/v1/projects/{pid}/workflows")).json()} == {
        "onboarding",
        "offboarding",
    }
    routing = (await client.get(f"/api/v1/projects/{pid}/workflow-routing")).json()
    assert routing["default"] == "onboarding"
    assert routing["rules"][0]["when"]["labels_any"] == ["offboarding", "leaver"]
    agents = {a["slug"]: a for a in (await client.get(f"{ORG}/agents")).json()}
    assert {"onboarding-coordinator", "offboarding-coordinator"} <= set(agents)
    detail = (await client.get(f"{ORG}/agents/onboarding-coordinator")).json()
    (version,) = detail["versions"]
    assert version["created_by"] == "template:joiners-leavers"
    assert [s["slug"] for s in version["spec"]["skills"]] == ["onboarding-procedure", "access-profiles"]
    skills = {s["slug"]: s for s in (await client.get(f"{ORG}/skills")).json()}
    assert skills["access-profiles"]["latest_version"] == 1
    assert skills["onboarding-procedure"]["latest_version"] == 1


async def test_a_second_project_does_not_rewrite_what_the_organisation_has_evolved(
    client: AsyncClient, admin: str
) -> None:
    await _project(client)
    read = (await client.get(f"{ORG}/skills/access-profiles/versions/1")).json()
    files = {
        **read["files"],
        "SKILL.md": read["files"]["SKILL.md"] + "\n| Lawyer | `legal` | `litigation` |\n",
    }
    published = await client.post(f"{ORG}/skills/access-profiles/versions", json={"files": files})
    assert published.status_code == 201, published.text
    await _project(client, slug="hr-lyon")
    skills = {s["slug"]: s for s in (await client.get(f"{ORG}/skills")).json()}
    assert skills["access-profiles"]["latest_version"] == 2, "the organisation's version stands"
    detail = (await client.get(f"{ORG}/agents/onboarding-coordinator")).json()
    assert len(detail["versions"]) == 1, "the agent is not reinstalled"


async def test_a_departure_is_born_in_its_workflow_and_its_fields_are_those_of_the_departure(
    client: AsyncClient, admin: str
) -> None:
    pid = (await _project(client))["id"]
    tracker = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    fields = {
        "full_name": "Paul Martin",
        "upn": "paul@acme.test",
        "last_day": "2026-11-30",
        "badge_uid": "04A1B2C3",
        "serial": "PC-0042",
        "address": "12 Peace Street, Paris",
    }
    departure = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Departure of Paul", "labels": ["leaver"], "fields": fields, "start": False},
    )
    assert departure.status_code == 201, departure.text
    assert departure.json()["state"] == "announced" and departure.json()["workflow_name"] == "offboarding"
    incomplete = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Departure", "labels": ["leaver"], "fields": {"full_name": "x"}, "start": False},
    )
    assert incomplete.status_code == 422 and "upn" in incomplete.text


async def test_a_template_skill_reads_nothing_outside_itself(
    client: AsyncClient, admin: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = tmp_path / "joiners-leavers"
    shutil.copytree(TEMPLATE, copy)
    (copy / "skills" / "access-profiles" / "leak.md").symlink_to("/etc/hostname")
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(tmp_path))
    refusal = await client.post(
        f"{ORG}/projects",
        json={
            "slug": "hr",
            "name": "hr",
            "template_ref": "joiners-leavers",
            "config": {"slug": "hr", "org": "varga"},
        },
    )
    assert refusal.status_code == 422 and "symbolic link" in refusal.text


async def test_check_holds_or_refuses_and_an_mcp_server_returns_what_it_structured(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import choregos_adapters
    from choregos_adapters.fakes.hr import FakeSupplier, mcp_server
    from choregos_api.db.models import Action, ConnectorOperation, OrgConnector, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effects import EffectContext, EffectRefused, effect, effect_policy

    assert effect_policy("check") == "allowed", "a check touches nothing outside"
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", mcp_server(FakeSupplier()))
    async with session_scope() as session:
        proj = await session.get(Project, project["id"])
        assert proj is not None
        supplier = OrgConnector(org_id=proj.org_id, name="supplier", kind="mcp", type="mcp",
                                config={"url": "https://supplier.test/mcp"})  # fmt: skip
        session.add(supplier)
        await session.flush()
        session.add(
            ConnectorOperation(
                org_id=proj.org_id,
                connector_id=supplier.id,
                name="order_laptop",
                access="write",
                policy="approval",
                groups=[],
            )
        )
        action = Action(
            org_id=proj.org_id,
            project_id=proj.id,
            origin="transition",
            kind="x",
            title="x",
            params={},
            effects=[],
            proposed_by={},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        ctx = EffectContext(session, action, proj)
        order = await effect("connector.call")(
            ctx,
            {"connector": "supplier", "operation": "order_laptop",
             "arguments": {"reference": "RH-1", "model": "p14", "upn": "lea"}},
        )  # fmt: skip
        assert order["serial"].startswith("PC-"), "the structured object, not the protocol's envelope"
        assert (await effect("check")(ctx, {"condition": "True", "reason": "everything holds"}))["ok"] is True
        with pytest.raises(EffectRefused, match="check failed: the active badge"):
            await effect("check")(ctx, {"condition": "False", "reason": "the active badge"})
