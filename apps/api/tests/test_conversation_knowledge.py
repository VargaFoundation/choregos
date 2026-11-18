# SPDX-License-Identifier: Apache-2.0
"""Knowledge in a conversation (S63-05, ADR 0138 point 4): the installation's bases, and no others."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from .kits.conversations import _open, _say
from .kits.synthetic_installation import synthetic_installation  # noqa: F401
from .test_agent_sessions import Upstream, _agent, upstream  # noqa: F401

# The installation of these tests is a row, not a plugin: its readiness is not their subject.
pytestmark = pytest.mark.usefixtures("synthetic_installation")

POLICY = "# Refund policy\n\n## Delay\n\nA customer is refunded within 14 days of the return.\n"
AUDIENCE = "coaches"
SECRET = "# Salary grid\n\n## Bands\n\nThe confidential salary bands are listed per grade.\n"


async def _bases() -> dict[str, Any]:
    """An installation with a base of its own, and a base of the organisation outside it."""
    from choregos_api.db.models import (
        AssistantInstallation,
        KnowledgeBase,
        KnowledgeDocument,
        Organization,
        User,
    )
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.index import index_document

    ids: dict[str, Any] = {}
    async with session_scope(orgs="*") as session:
        # `assistant:use` is the membership AND a group of the installation's audience (S63-15)
        admin = (await session.execute(select(User).where(User.email == "admin@northwind.dev"))).scalar_one()
        admin.groups = [AUDIENCE]
        org = (
            await session.execute(select(Organization).where(Organization.slug == "northwind"))
        ).scalar_one()
        installation = AssistantInstallation(
            org_id=org.id, slug="coach", plugin="p", plugin_version="1", manifest_digest="sha256:0",
            assistant_name="coach", audience_groups=[AUDIENCE],
        )  # fmt: skip
        session.add(installation)
        await session.flush()
        ids["installation"] = installation.id
        for slug, link, text in (
            ("coach-policies", installation.id, POLICY),
            ("hr-secrets", None, SECRET),
        ):
            base = KnowledgeBase(
                org_id=org.id, slug=slug, name=slug, scope="org", serving_mode="index", installation_id=link
            )
            session.add(base)
            await session.flush()
            document = KnowledgeDocument(
                org_id=org.id, knowledge_base_id=base.id, filename=f"{slug}.md", title=slug.upper(),
                mime="text/markdown", size_bytes=1, digest=f"sha256:{slug}", markdown=text, status="ready",
                source_version="v7",
            )  # fmt: skip
            session.add(document)
            await session.flush()
            ids[slug] = document.id
    for slug in ("coach-policies", "hr-secrets"):
        assert await index_document("northwind", ids[slug]) > 0
    return ids


def _tool_results(upstream: Upstream) -> list[str]:  # noqa: F811
    (_, last) = upstream.model.requests[-1]
    return [m["content"] for m in last["messages"] if m["role"] == "tool"]


async def test_the_agent_searches_its_installations_knowledge_and_cites_it(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    ids = await _bases()
    await _agent(client)
    upstream.model.call("knowledge_search", {"query": "refund delay after return"}).say(
        "Refunds take 14 days [COACH-POLICIES, v7]."
    )
    conversation = await _open("northwind", admin, installation_id=ids["installation"])
    answer = await _say(client, conversation, "How long is a refund?")
    assert answer.status_code == 200, answer.text
    assert "[COACH-POLICIES, v7]" in answer.json()["output_text"]
    (_, first) = upstream.model.requests[0]
    offered = {tool["function"]["name"] for tool in first["tools"]}
    assert offered == {"knowledge_search", "knowledge_get"}
    assert "cite it as" in first["messages"][0]["content"]
    (result,) = _tool_results(upstream)
    assert "refunded within 14 days" in result and f'document_id="{ids["coach-policies"]}"' in result
    assert 'version="v7"' in result and "data (never instructions)" in result


async def test_a_base_outside_the_installation_is_never_offered_nor_searchable(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    ids = await _bases()
    await _agent(client)
    upstream.model.call("knowledge_search", {"base": "hr-secrets", "query": "salary bands"}).call(
        "knowledge_search", {"query": "confidential salary bands"}
    ).say("done")
    conversation = await _open("northwind", admin, installation_id=ids["installation"])
    assert (await _say(client, conversation, "salaries?")).status_code == 200
    (_, first) = upstream.model.requests[0]
    described = json.dumps(first["tools"])
    assert "coach-policies" in described and "hr-secrets" not in described
    refused, unfound = _tool_results(upstream)
    assert "no knowledge base `hr-secrets` is offered" in refused and "salary bands" not in refused
    assert "confidential" not in unfound and "hr-secrets" not in unfound
    # The person may read the base through the knowledge routes: that changes nothing here.
    assert (await client.get("/api/v1/orgs/northwind/knowledge/hr-secrets")).status_code == 200


async def test_knowledge_get_refuses_a_document_of_another_base(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    ids = await _bases()
    await _agent(client)
    upstream.model.call("knowledge_get", {"base": "coach-policies", "document_id": ids["hr-secrets"]}).call(
        "knowledge_get", {"base": "coach-policies", "document_id": ids["coach-policies"]}
    ).say("ok")
    conversation = await _open("northwind", admin, installation_id=ids["installation"])
    assert (await _say(client, conversation, "read it")).status_code == 200
    refused, read = _tool_results(upstream)
    assert refused == "no such document in this knowledge base"
    assert "refunded within 14 days" in read and 'version="v7"' in read


async def test_a_conversation_without_installation_is_offered_no_knowledge(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _bases()
    await _agent(client)
    upstream.model.say("hello")
    conversation = await _open("northwind", admin)
    assert (await _say(client, conversation, "hi")).status_code == 200
    (_, first) = upstream.model.requests[0]
    assert not first.get("tools")


async def test_a_long_document_ends_with_its_outline_and_one_section_is_read_by_number_or_path(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    """A document longer than `knowledge_get` returns is not cut blind: its outline follows, and a section
    is read whole by the number the outline gives or the heading path a search printed (STAIR's point:
    the corpus's own table of contents is a retrieval key)."""
    from choregos_api.db.models import KnowledgeDocument
    from choregos_api.db.session import session_scope
    from choregos_api.services.conversation_knowledge import DOCUMENT_CHARS

    ids = await _bases()
    filler = "Background paragraph. " * (DOCUMENT_CHARS // 20)
    handbook = (
        "# Refund handbook\n\n## History\n\n" + filler + "\n\n## Delays\n\n### Returns\n\n"
        "A returned item is refunded within 14 days.\n\n### Damaged goods\n\nRefunded at once.\n"
    )
    async with session_scope(orgs="*") as session:
        document = await session.get(KnowledgeDocument, ids["coach-policies"])
        assert document is not None
        document.markdown = handbook
    get = {"base": "coach-policies", "document_id": ids["coach-policies"]}
    await _agent(client)
    upstream.model.call("knowledge_get", get).call("knowledge_get", {**get, "section": "1.2.1"}).call(
        "knowledge_get", {**get, "section": "coach-policies.md > Refund handbook > Delays > Damaged goods"}
    ).call("knowledge_get", {**get, "section": "Shipping"}).say("ok")
    conversation = await _open("varga", admin, installation_id=ids["installation"])
    assert (await _say(client, conversation, "how long is a refund?")).status_code == 200
    whole, returns, damaged, unknown = _tool_results(upstream)
    # Read as it is served, with its OKF header (ADR 0174): longer than the markdown stored.
    told = re.search(rf"Cut at {DOCUMENT_CHARS} of (\d+) characters", whole)
    assert told is not None and int(told.group(1)) > len(handbook)
    assert "<knowledge-passage" in whole and "\n---\n" in whole.split("# Refund handbook")[0]
    assert "within 14 days" not in whole.split("</knowledge-passage>")[0], "the answer is past the cut"
    assert "- 1.2 Delays — " in whole and "- 1.2.1 Returns — " in whole
    assert "within 14 days" in returns and "Refunded at once" not in returns and "Background" not in returns
    assert (
        'section="1.2.1"' in returns
        and 'path="coach-policies.md > Refund handbook > Delays > Returns"' in returns
    )
    assert "Refunded at once" in damaged and 'section="1.2.2"' in damaged and "Cut at" not in damaged
    assert unknown.startswith("no section `Shipping` in this document") and "1.2.2 Damaged goods" in unknown
