# SPDX-License-Identifier: Apache-2.0
"""Bound knowledge in a conversation (S66-12, ADR 0159 points 4 and 6).

A base an installation's knowledge need is bound to is searched like its seeded copies, and cited by its
slug; a person who may not read a bound base gets nothing of it at that turn (not named, not searchable,
not readable by id), and the next turn follows a grant; a disabled binding is never served.
"""

# ruff: noqa: F811 - the fixtures imported from the kits are redefined by the parameters that name them

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from .agent_scopes_kit import add_member, as_user
from .kits.conversations import _open, _say
from .kits.synthetic_installation import synthetic_installation  # noqa: F401
from .test_agent_sessions import Upstream, _agent, upstream  # noqa: F401

pytestmark = pytest.mark.usefixtures("synthetic_installation")

AUDIENCE = "analysts"
READER = "reader@varga.dev"
TEXTS = {
    "coach-policies": "# Refund policy\n\nA customer is refunded within 14 days of the return.\n",
    "public-notes": "# Market notes\n\nThe house expects rates to stay flat this quarter.\n",
    "board-notes": "# Board notes\n\nThe confidential merger closes in March.\n",
}


async def _setup() -> dict[str, Any]:
    """An installation with its seeded copy, a bound base everyone reads, a bound restricted base, and a
    person of the audience who is not an administrator."""
    from choregos_api.db.models import (
        AssistantInstallation,
        AssistantKnowledgeBinding,
        KnowledgeBase,
        KnowledgeDocument,
        Organization,
        User,
    )
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.index import index_document

    ids: dict[str, Any] = {}
    async with session_scope(orgs="*") as session:
        reader = (await session.execute(select(User).where(User.email == READER))).scalar_one()
        reader.groups = [AUDIENCE]
        ids["reader"] = reader.id
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        ids["org"] = org.id
        installation = AssistantInstallation(
            org_id=org.id, slug="analyst", plugin="p", plugin_version="1", manifest_digest="sha256:0",
            assistant_name="analyst", audience_groups=[AUDIENCE],
        )  # fmt: skip
        session.add(installation)
        await session.flush()
        ids["installation"] = installation.id
        for slug, text in TEXTS.items():
            base = KnowledgeBase(
                org_id=org.id, slug=slug, name=slug, scope="org", serving_mode="index",
                restricted=slug == "board-notes",
                installation_id=installation.id if slug == "coach-policies" else None,
            )  # fmt: skip
            session.add(base)
            await session.flush()
            ids[f"base:{slug}"] = base.id
            if slug != "coach-policies":
                session.add(
                    AssistantKnowledgeBinding(
                        org_id=org.id, installation_id=installation.id, name="house_research",
                        knowledge_base_id=base.id,
                    )
                )  # fmt: skip
            document = KnowledgeDocument(
                org_id=org.id, knowledge_base_id=base.id, filename=f"{slug}.md", title=slug.upper(),
                mime="text/markdown", size_bytes=1, digest=f"sha256:{slug}", markdown=text, status="ready",
                source_version="v3",
            )  # fmt: skip
            session.add(document)
            await session.flush()
            ids[slug] = document.id
    for slug in TEXTS:
        assert await index_document("varga", ids[slug]) > 0
    return ids


def _offered(upstream: Upstream, request: int) -> str:
    (_, sent) = upstream.model.requests[request]
    return json.dumps(sent.get("tools") or [])


def _tool_results(upstream: Upstream) -> list[str]:
    (_, last) = upstream.model.requests[-1]
    return [m["content"] for m in last["messages"] if m["role"] == "tool"]


async def _reader(client: AsyncClient) -> dict[str, Any]:
    await _agent(client)
    await add_member(client, READER, "developer")
    ids = await _setup()
    await as_user(client, READER)
    return ids


async def test_a_bound_base_is_searched_and_cited_by_its_slug(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,
) -> None:
    ids = await _reader(client)
    upstream.model.call("knowledge_search", {"base": "public-notes", "query": "rates this quarter"}).call(
        "knowledge_get", {"base": "public-notes", "document_id": ids["public-notes"]}
    ).say("Rates stay flat [PUBLIC-NOTES, v3].")
    conversation = await _open("varga", READER, installation_id=ids["installation"])
    answer = await _say(client, conversation, "What about rates?")
    assert answer.status_code == 200, answer.text
    assert "coach-policies" in _offered(upstream, 0) and "public-notes" in _offered(upstream, 0)
    found, read = _tool_results(upstream)
    assert "stay flat" in found and 'base="public-notes"' in found and 'version="v3"' in found
    assert "stay flat" in read and 'base="public-notes"' in read


async def test_a_person_who_may_not_read_a_bound_base_gets_nothing_of_it_until_a_grant(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,
) -> None:
    from choregos_api.db.models import KnowledgeGrant
    from choregos_api.db.session import session_scope

    ids = await _reader(client)
    upstream.model.call("knowledge_search", {"base": "board-notes", "query": "merger"}).call(
        "knowledge_search", {"query": "confidential merger closes"}
    ).call("knowledge_get", {"base": "public-notes", "document_id": ids["board-notes"]}).say("nothing")
    conversation = await _open("varga", READER, installation_id=ids["installation"])
    assert (await _say(client, conversation, "Any merger?")).status_code == 200
    assert "board-notes" not in _offered(upstream, 0)
    named, searched, by_id = _tool_results(upstream)
    assert "no knowledge base `board-notes` is offered" in named
    assert "merger" not in searched and "board-notes" not in searched
    assert by_id == "no such document in this knowledge base"

    async with session_scope(orgs="*") as session:
        session.add(
            KnowledgeGrant(org_id=ids["org"], knowledge_base_id=ids["base:board-notes"], subject_type="user",
                           subject=ids["reader"], role="viewer")
        )  # fmt: skip
    upstream.model.call("knowledge_search", {"base": "board-notes", "query": "merger"}).say("March.")
    assert (await _say(client, conversation, "And now?")).status_code == 200
    assert "board-notes" in _offered(upstream, -2)
    (found,) = _tool_results(upstream)[-1:]
    assert "closes in March" in found and 'base="board-notes"' in found


async def test_a_disabled_binding_is_never_served(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,
) -> None:
    from choregos_api.db.models import AssistantKnowledgeBinding
    from choregos_api.db.session import session_scope

    ids = await _reader(client)
    async with session_scope(orgs="*") as session:
        await session.execute(update(AssistantKnowledgeBinding).values(enabled=False))
    upstream.model.say("hello")
    conversation = await _open("varga", READER, installation_id=ids["installation"])
    assert (await _say(client, conversation, "hi")).status_code == 200
    offered = _offered(upstream, 0)
    assert "coach-policies" in offered and "public-notes" not in offered and "board-notes" not in offered
