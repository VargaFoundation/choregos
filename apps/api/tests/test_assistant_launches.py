# SPDX-License-Identifier: Apache-2.0
"""Binding an assistant's launches to a project's workflow, and the readiness they make (S66-22, ADR 0158).

A required launch left unbound makes the installation `incomplete`; binding it to a project's active workflow
makes it `ready`. Binding needs `assistant:manage` on the installation AND `item:control` in the project; a
personal space, a workflow the project does not have and inputs the workflow cannot take are refused with a
422 and nothing changes. A plugin upgrade that changes a launch follows the need-change path (S63-22).
"""

# ruff: noqa: F811 - the `offline` fixture imported from the kit is redefined by the parameter that names it

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from httpx import AsyncClient

from .agent_scopes_kit import add_member, as_user
from .kits.assistant_install import FIXTURE, ORG, offline  # noqa: F401
from .kits.assistant_launches import (
    DECLARATION,
    INPUT_SCHEMA,
    LAUNCH,
    OWNERS,
    REVIEW,
    SLUG,
    WORKFLOW,
    bind_launch,
    declare,
    install,
    items,
    publish_review,
    set_groups,
)


async def test_a_required_unbound_launch_is_incomplete_and_binding_it_makes_it_ready(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    await install(client, offline)
    await publish_review(client, project["id"])
    readiness = (await client.get(f"{SLUG}/readiness")).json()
    assert readiness["status"] == "incomplete"
    assert await items(client) == [("launch_unbound", LAUNCH)]
    (item,) = readiness["items"]
    assert item["fix_by"] == "owners" and item["groups"] == [OWNERS] and item["need"] is None

    bound = await bind_launch(client)
    assert bound.status_code == 200, bound.text
    assert bound.json()["status"] == "ready"
    (launch,) = bound.json()["launches"]
    assert launch == {
        "name": LAUNCH,
        "description": DECLARATION["description"],
        "required": True,
        "input_schema": INPUT_SCHEMA,
        "project": "billing-api",
        "workflow": WORKFLOW,
        "enabled": True,
        "bound_by": "admin@varga.dev",
    }
    assert (await client.get(f"{SLUG}/readiness")).json() == {"status": "ready", "items": []}


async def test_an_optional_launch_left_unbound_is_not_a_gap(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    await install(client, offline, {**DECLARATION, "required": False})
    assert (await client.get(f"{SLUG}/readiness")).json()["status"] == "ready"
    (launch,) = (await client.get(SLUG)).json()["launches"]
    assert launch["project"] is None and launch["enabled"] is False


async def test_a_required_launch_cannot_be_switched_off(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    await install(client, offline)
    await publish_review(client, project["id"])
    refused = await bind_launch(client, enabled=False)
    assert refused.status_code == 422 and "required launch" in refused.text


async def test_binding_needs_the_right_to_create_work_items_in_the_project(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    await install(client, offline)
    await publish_review(client, project["id"])
    # An owner of the installation (its owner groups) who only reads the project.
    await add_member(client, "owner@varga.dev", "viewer")
    await set_groups("owner@varga.dev", [OWNERS])
    await as_user(client, "owner@varga.dev")
    refused = await bind_launch(client)
    assert refused.status_code == 403, refused.text
    assert "create work items" in refused.text
    after = (await client.get(SLUG)).json()
    assert after["launches"][0]["project"] is None and after["status"] == "incomplete"

    # The same owner, now a developer of the project: the binding is theirs to make.
    await add_member(client, "owner@varga.dev", "developer", project="billing-api")
    await as_user(client, "owner@varga.dev")
    accepted = await bind_launch(client)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["launches"][0]["bound_by"] == "owner@varga.dev"


async def test_a_developer_of_the_project_who_does_not_manage_the_assistant_cannot_bind(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    await install(client, offline)
    await publish_review(client, project["id"])
    await add_member(client, "dev@varga.dev", "developer", project="billing-api")
    await as_user(client, "dev@varga.dev")
    assert (await bind_launch(client)).status_code == 403


async def test_a_project_of_another_organisation_or_none_is_not_a_project(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    await install(client, offline)
    unknown = await bind_launch(client, project="no-such-project")
    assert unknown.status_code == 422 and "is not a project of varga" in unknown.text


async def test_a_missing_workflow_inputs_it_cannot_take_and_a_personal_space_are_refused(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    from choregos_api.db.models import Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.services.personal_spaces import personal_space
    from sqlalchemy import select

    await install(client, offline)
    missing = await bind_launch(client)
    assert missing.status_code == 422 and "no active workflow `review`" in missing.text

    # The workflow requires an input the launch does not fill.
    demanding = REVIEW.replace("required: [portfolio]", "required: [portfolio, owner]").replace(
        "      review_date: {type: string}", "      review_date: {type: string}\n      owner: {type: string}"
    )
    await publish_review(client, project["id"], demanding)
    mismatch = await bind_launch(client)
    assert mismatch.status_code == 422, mismatch.text
    assert "requires the input `owner`" in mismatch.text

    # The workflow's closed inputs do not accept a property the launch fills.
    closed = REVIEW.replace("      review_date: {type: string}\n", "")
    await publish_review(client, project["id"], closed)
    refused = await bind_launch(client)
    assert refused.status_code == 422 and "fills `review_date`" in refused.text

    async with session_scope(orgs="*") as session:
        organisation = (await session.execute(select(Organization))).scalar_one()
        user = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        space = (await personal_space(session, organisation, user)).slug
    personal = await bind_launch(client, project=space)
    assert personal.status_code == 422 and "personal space" in personal.text
    assert (await client.get(SLUG)).json()["launches"][0]["project"] is None


async def test_a_workflow_that_stops_fitting_is_a_readiness_item(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    await install(client, offline)
    await publish_review(client, project["id"])
    assert (await bind_launch(client)).status_code == 200
    demanding = REVIEW.replace("required: [portfolio]", "required: [portfolio, review_date]")
    await publish_review(client, project["id"], demanding)
    assert await items(client) == [("launch_inputs_mismatch", LAUNCH)]
    assert (await client.get(f"{SLUG}/readiness")).json()["status"] == "incomplete"


async def test_deleting_the_project_unbinds_the_launch(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    await install(client, offline)
    await publish_review(client, project["id"])
    assert (await bind_launch(client)).status_code == 200
    async with session_scope(orgs="*") as session:
        row = await session.get(Project, project["id"])
        assert row is not None
        await session.delete(row)
    assert await items(client) == [("launch_unbound", LAUNCH)]


def _publish(root: Path, version: str, launches: list[dict[str, Any]]) -> None:
    target = root / "investment-assistant" / version
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(FIXTURE, target)
    manifest = target / "plugin.yaml"
    manifest.write_text(manifest.read_text().replace("version: 0.1.0", f"version: {version}"))
    if launches:
        declare(root, *launches, version=version)
    else:
        _without_launches(root, version)


def _without_launches(root: Path, version: str) -> None:
    path = root / "investment-assistant" / version / "assistants" / "investment-assistant.yaml"
    document = yaml.safe_load(path.read_text())
    document.pop("launches", None)
    path.write_text(yaml.safe_dump(document, sort_keys=False))


async def _upgrade(client: AsyncClient, version: str) -> None:
    response = await client.post(f"{ORG}/plugins/investment-assistant/upgrade", json={"version": version})
    assert response.status_code == 200, response.text


async def test_an_upgrade_that_drops_the_launch_switches_its_binding_off_and_keeps_it(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    await install(client, offline)
    await publish_review(client, project["id"])
    assert (await bind_launch(client)).status_code == 200
    _publish(offline, "0.2.0", [])
    await _upgrade(client, "0.2.0")
    body = (await client.get(SLUG)).json()
    assert body["launches"] == [] and body["status"] == "ready"
    from choregos_api.db.models import AssistantLaunchBinding
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        (kept,) = (await session.execute(select(AssistantLaunchBinding))).scalars()
        assert kept.enabled is False and kept.workflow == WORKFLOW


async def test_an_upgrade_whose_launch_no_longer_fits_switches_it_off_with_an_item(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    await install(client, offline)
    await publish_review(client, project["id"])
    assert (await bind_launch(client)).status_code == 200
    # The new version fills a property the workflow's closed inputs refuse.
    wider = {**INPUT_SCHEMA, "properties": {**INPUT_SCHEMA["properties"], "currency": {"type": "string"}}}
    _publish(offline, "0.2.0", [{**DECLARATION, "input_schema": wider}])
    await _upgrade(client, "0.2.0")
    (launch,) = (await client.get(SLUG)).json()["launches"]
    assert launch["enabled"] is False and launch["project"] == "billing-api"
    assert await items(client) == [("launch_inputs_mismatch", LAUNCH)]


async def test_an_upgrade_that_adds_a_required_launch_leaves_it_unbound(
    client: AsyncClient, project: dict[str, Any], offline: Path
) -> None:
    declare_none = [{**DECLARATION, "name": "other_review", "required": False}]
    await install(client, offline, *declare_none)
    assert (await client.get(f"{SLUG}/readiness")).json()["status"] == "ready"
    _publish(offline, "0.2.0", [*declare_none, DECLARATION])
    await _upgrade(client, "0.2.0")
    assert await items(client) == [("launch_unbound", LAUNCH)]
