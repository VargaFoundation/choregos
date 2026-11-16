"""What the launch tests share (S66-22, ADR 0158): the fixture plugin with a launch, a project whose workflow
serves it, and the helpers that install and bind them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from httpx import AsyncClient

from .assistant_install import MARKET, NEWS, ORG, _bind, _connector, _install, _plugin

SLUG = f"{ORG}/assistants/investment-assistant"
LAUNCH = "portfolio_review"
WORKFLOW = "review"
AUDIENCE = "grp-reviewers"
OWNERS = "grp-review-owners"
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["portfolio"],
    "properties": {"portfolio": {"type": "string", "minLength": 1}, "review_date": {"type": "string"}},
    "additionalProperties": False,
}
DECLARATION: dict[str, Any] = {
    "name": LAUNCH,
    "description": "Ask the review team for a portfolio review document.",
    "input_schema": INPUT_SCHEMA,
    "required": True,
}

REVIEW = """apiVersion: choregos/v1
kind: Workflow
metadata:
  name: review
  version: 1
  inputs:
    type: object
    required: [portfolio]
    additionalProperties: false
    properties:
      portfolio: {type: string, minLength: 1}
      review_date: {type: string}
actors:
  reviewers: {type: human, group: reviewers}
states:
  writing: {display: Writing, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-done, from: writing, to: done, by: reviewers}
"""


def manifest_path(root: Path, version: str = "0.1.0") -> Path:
    return root / "investment-assistant" / version / "assistants" / "investment-assistant.yaml"


def declare(root: Path, *launches: dict[str, Any], version: str = "0.1.0") -> None:
    """The fixture plugin's assistant, in the offline directory, declaring `launches`."""
    path = manifest_path(root, version)
    document = yaml.safe_load(path.read_text())
    document["launches"] = list(launches) or [DECLARATION]
    path.write_text(yaml.safe_dump(document, sort_keys=False))


async def publish_review(client: AsyncClient, project_id: str, yaml_text: str = REVIEW) -> None:
    """As the organisation's administrator: the project's workflow `review`."""
    response = await client.put(
        f"/api/v1/projects/{project_id}/workflows/{WORKFLOW}", json={"yaml": yaml_text}
    )
    assert response.status_code == 200, response.text


async def install(client: AsyncClient, root: Path, *launches: dict[str, Any]) -> None:
    """The plugin with its launches, the assistant installed for `AUDIENCE`, its tool needs bound."""
    declare(root, *launches)
    await _plugin(client)
    installed = await _install(client, audience_groups=[AUDIENCE], owner_groups=[OWNERS])
    assert installed.status_code == 201, installed.text
    await _connector("news-feed", NEWS)
    await _connector("markets", MARKET)
    assert (await _bind(client, "news", "news-feed")).status_code == 200
    assert (await _bind(client, "market_data", "markets")).status_code == 200


async def bind_launch(
    client: AsyncClient, project: str = "billing-api", workflow: str = WORKFLOW, **extra: Any
) -> Any:
    return await client.put(
        f"{SLUG}/launches/{LAUNCH}", json={"project": project, "workflow": workflow, **extra}
    )


async def items(client: AsyncClient, kind_prefix: str = "launch_") -> list[tuple[str, str | None]]:
    response = await client.get(f"{SLUG}/readiness")
    assert response.status_code == 200, response.text
    return [
        (i["kind"], i.get("launch")) for i in response.json()["items"] if i["kind"].startswith(kind_prefix)
    ]


async def installation_id() -> str:
    from choregos_api.db.models import AssistantInstallation
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        return str(
            (
                await session.execute(
                    select(AssistantInstallation.id).where(
                        AssistantInstallation.slug == "investment-assistant"
                    )
                )
            ).scalar_one()
        )


async def set_groups(email: str, groups: list[str]) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        (await session.execute(select(User).where(User.email == email))).scalar_one().groups = groups


async def work_items(project_slug: str = "billing-api") -> list[Any]:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        rows = await session.execute(
            select(WorkItem)
            .join(Project, Project.id == WorkItem.project_id)
            .where(Project.slug == project_slug)
        )
        found = list(rows.scalars())
        for row in found:
            session.expunge(row)
        return found
