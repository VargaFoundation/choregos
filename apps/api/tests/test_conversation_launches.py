# SPDX-License-Identifier: Apache-2.0
"""Launches in a conversation (S66-22, ADR 0158): the assistant proposes, the person confirms as themself.

A bound launch is offered as `launch__<name>`; calling it starts nothing and stores a proposed action of kind
`launch`. The conversation's owner confirms it: the work item is created in the bound project, under the
bound workflow, with THEIR identity (reporter, initiator) and only if THEY may create work items there;
otherwise 403 `launch_forbidden` and nothing is created. A launch rebound since the proposal is not started.
The door lists the proposal with its console link and starts nothing. `launch_status` reports the work item
and its artefacts.
"""

# ruff: noqa: F811 - fixtures imported from the kits are redefined by the parameters that name them

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from .agent_scopes_kit import add_member, as_user, make_project
from .kits.assistant_install import offline  # noqa: F401
from .kits.assistant_launches import (
    AUDIENCE,
    LAUNCH,
    WORKFLOW,
    bind_launch,
    install,
    installation_id,
    publish_review,
    set_groups,
    work_items,
)
from .kits.conversations import _open, _say
from .kits.synthetic_installation import synthetic_installation  # noqa: F401
from .test_agent_sessions import Upstream, _agent, upstream  # noqa: F401

# The installation's readiness is the subject of `test_assistant_launches.py`, not of these tests.
pytestmark = pytest.mark.usefixtures("synthetic_installation")

ME = "/api/v1/me/conversations"
TOOL = f"launch__{LAUNCH}"
ARGUMENTS = {
    "title": "Review of the growth portfolio",
    "inputs": {"portfolio": "growth", "review_date": "2026-12-01"},
}
ADMIN = "admin@varga.dev"


async def _bound(client: AsyncClient, project: dict[str, Any], offline_root: Path) -> None:
    await install(client, offline_root)
    await publish_review(client, project["id"])
    assert (await bind_launch(client)).status_code == 200
    await _agent(client)


async def _conversation(email: str = ADMIN) -> str:
    await set_groups(email, [AUDIENCE])
    return await _open("varga", email, installation_id=await installation_id())


async def _propose(client: AsyncClient, upstream: Upstream, conversation: str, arguments: Any = None) -> Any:
    upstream.model.call(TOOL, arguments or ARGUMENTS).say("I proposed the review; please confirm it.")
    answer = await _say(client, conversation, "Ask the review team to look at my growth portfolio.")
    assert answer.status_code == 200, answer.text
    return (await client.get(f"{ME}/{conversation}/actions")).json()["items"]


def _told(upstream: Upstream) -> list[str]:
    (_, last) = upstream.model.requests[-1]
    return [str(m["content"]) for m in last["messages"] if m["role"] == "tool"]


async def _reads() -> list[tuple[str, str, str | None]]:
    from choregos_api.db.models import AssistantRead
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = await session.execute(select(AssistantRead).order_by(AssistantRead.at))
        return [(r.need, r.operation, r.work_item_key) for r in rows.scalars()]


async def test_a_launch_is_proposed_and_nothing_is_started(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    await _bound(client, project, offline)
    conversation = await _conversation()
    (action,) = await _propose(client, upstream, conversation)

    (_, first) = upstream.model.requests[0]
    offered = {tool["function"]["name"]: tool["function"] for tool in first["tools"]}
    assert {TOOL, "launch_status"} <= set(offered)
    assert offered[TOOL]["parameters"]["properties"]["inputs"]["required"] == ["portfolio"]
    assert "NOTHING was started" in _told(upstream)[0]

    assert action["kind"] == "launch" and action["status"] == "proposed"
    assert action["need"] == LAUNCH and action["operation"] == "start"
    assert action["arguments"] == ARGUMENTS and action["result"] is None
    assert action["target"]["project"] == "billing-api" and action["target"]["workflow"] == WORKFLOW
    assert "growth portfolio" in action["summary"]
    assert await work_items() == [], "a proposal files nothing"
    assert (LAUNCH, "propose", None) in await _reads()


async def test_inputs_the_launch_refuses_are_not_proposed(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    await _bound(client, project, offline)
    conversation = await _conversation()
    actions = await _propose(
        client, upstream, conversation, {"title": "Review", "inputs": {"review_date": "x"}}
    )
    assert actions == []
    assert "do not match the launch's schema" in _told(upstream)[0]


async def test_confirming_creates_the_work_item_as_the_person(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    await _bound(client, project, offline)
    conversation = await _conversation()
    (action,) = await _propose(client, upstream, conversation)

    confirmed = await client.post(f"{ME}/{conversation}/actions/{action['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "executed" and body["executed_at"] is not None
    result = body["result"]
    assert result["project"] == "billing-api" and result["workflow"] == WORKFLOW
    assert result["url"].endswith(f"/p/billing-api/items/{result['work_item_id']}")

    (item,) = await work_items()
    assert item.id == result["work_item_id"] and item.tracker_key == result["work_item_key"]
    assert item.title == ARGUMENTS["title"] and item.fields == ARGUMENTS["inputs"]
    assert item.reporter == ADMIN and item.initiated_by == f"person:{ADMIN}"
    assert conversation in (item.body_snapshot or "")
    fetched = (await client.get(f"/api/v1/work-items/{item.id}")).json()
    assert fetched["workflow_name"] == WORKFLOW

    again = await client.post(f"{ME}/{conversation}/actions/{action['id']}/confirm")
    assert again.status_code == 409 and again.json()["code"] == "action_executed"
    assert len(await work_items()) == 1, "a launch is started once"


async def test_a_person_without_rights_in_the_project_cannot_start_it(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    """The decisive check is the confirming person's own request: rights lost after the proposal leave
    nothing created."""
    from choregos_api.db.models import Membership, Project
    from choregos_api.db.session import session_scope

    await _bound(client, project, offline)
    await add_member(client, "dev@varga.dev", "viewer")
    await add_member(client, "dev@varga.dev", "developer", project="billing-api")
    conversation = await _conversation("dev@varga.dev")
    await as_user(client, "dev@varga.dev")
    (action,) = await _propose(client, upstream, conversation)

    async with session_scope(orgs="*") as session:
        project_id = (
            await session.execute(select(Project.id).where(Project.slug == "billing-api"))
        ).scalar_one()
        await session.execute(delete(Membership).where(Membership.project_id == project_id))
    await as_user(client, "dev@varga.dev")  # a new sign-in carries the roles as they are now
    refused = await client.post(f"{ME}/{conversation}/actions/{action['id']}/confirm")
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "launch_forbidden" and "billing-api" in refused.json()["detail"]
    assert await work_items() == [], "nothing is created"
    (still,) = (await client.get(f"{ME}/{conversation}/actions")).json()["items"]
    assert still["status"] == "proposed"


async def test_a_person_who_may_not_create_work_items_is_told_and_nothing_is_proposed(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    await _bound(client, project, offline)
    await add_member(client, "reader@varga.dev", "viewer")
    conversation = await _conversation("reader@varga.dev")
    await as_user(client, "reader@varga.dev")
    assert await _propose(client, upstream, conversation) == []
    assert "may not start work in the project `billing-api`" in _told(upstream)[0]


async def test_a_launch_rebound_since_the_proposal_is_not_started(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    await _bound(client, project, offline)
    conversation = await _conversation()
    (action,) = await _propose(client, upstream, conversation)
    other = await make_project(client, "reviews")
    await publish_review(client, other["id"])
    assert (await bind_launch(client, project="reviews")).status_code == 200
    refused = await client.post(f"{ME}/{conversation}/actions/{action['id']}/confirm")
    assert refused.status_code == 409 and refused.json()["code"] == "launch_rebound"
    assert await work_items() == [] and await work_items("reviews") == []


async def test_a_refused_launch_starts_nothing(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    await _bound(client, project, offline)
    conversation = await _conversation()
    (action,) = await _propose(client, upstream, conversation)
    refused = await client.post(f"{ME}/{conversation}/actions/{action['id']}/refuse")
    assert refused.status_code == 200 and refused.json()["status"] == "refused"
    assert (await client.post(f"{ME}/{conversation}/actions/{action['id']}/confirm")).status_code == 409
    assert await work_items() == []


async def test_launch_status_reports_the_work_item_and_its_artefacts(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    from choregos_api.db.models import ArtifactRecord
    from choregos_api.db.session import session_scope

    await _bound(client, project, offline)
    conversation = await _conversation()
    (action,) = await _propose(client, upstream, conversation)
    confirmed = (await client.post(f"{ME}/{conversation}/actions/{action['id']}/confirm")).json()
    (item,) = await work_items()
    async with session_scope(orgs="*") as session:
        session.add(
            ArtifactRecord(
                org_id=await _org_id(session),
                work_item_id=item.id, name="review", version=1, type="markdown", kind="markdown",
                media_type="text/markdown", digest="sha256:" + "0" * 64, size=7, content="# Review",
                run_id="run-1",
            )
        )  # fmt: skip

    upstream.model.call("launch_status", {}).say("Your review is being written.")
    assert (await _say(client, conversation, "Where is my review?")).status_code == 200
    report = json.loads(_told(upstream)[-1])
    (line,) = report["launches"]
    assert line["action_id"] == action["id"] and line["status"] == "executed"
    assert line["work_item"] == confirmed["result"]["work_item_key"] and line["title"] == ARGUMENTS["title"]
    assert line["ended"] is False and line["state"] == item.state
    (artifact,) = line["artifacts"]
    assert artifact["name"] == "review" and artifact["version"] == 1
    assert artifact["content_url"].endswith(f"/api/v1/work-items/{item.id}/artifacts/review@1/content")
    assert "# Review" not in _told(upstream)[-1], "the content is not given to the conversation"
    assert (LAUNCH, "status", confirmed["result"]["work_item_key"]) in await _reads()


async def _org_id(session: Any) -> str:
    from choregos_api.db.models import Organization

    return str((await session.execute(select(Organization.id))).scalar_one())


async def test_an_unbound_launch_is_not_offered(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    await install(client, offline)
    await _agent(client)
    conversation = await _conversation()
    upstream.model.say("hello")
    assert (await _say(client, conversation, "hi")).status_code == 200
    (_, first) = upstream.model.requests[0]
    names = {tool["function"]["name"] for tool in first.get("tools") or []}
    assert TOOL not in names and "launch_status" not in names


async def test_the_mcp_door_lists_the_launch_with_a_console_link_and_starts_nothing(
    client: AsyncClient, project: dict[str, Any], offline: Path, upstream: Upstream
) -> None:
    from .kits.mcp_assistants import _call, _names

    await _bound(client, project, offline)
    conversation = await _conversation()
    (action,) = await _propose(client, upstream, conversation)
    created = await client.post("/api/v1/me/tokens", json={"name": "mcp", "scopes": ["mcp:read"]})
    token = str(created.json()["token"])
    names = await _names(client, token)
    assert not {n for n in names if "confirm" in n or n.startswith("launch")}, "the door starts nothing"
    answer = await _call(client, token, "get_conversation", {"conversation_id": conversation})
    (pending,) = answer["pending_actions"]
    assert pending["id"] == action["id"] and "growth portfolio" in pending["summary"]
    assert pending["confirm_url"].endswith(f"/conversations/{conversation}?action={action['id']}")
    assert await work_items() == []
