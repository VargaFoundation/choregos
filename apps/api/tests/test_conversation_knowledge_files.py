"""Knowledge in a conversation, what S63-05 left (ADR 0138 point 4): a `files` base is listed and read, and a
restricted base is served to the assistant whatever grants the person has (giving the assistant is the
control)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from .kits.conversations import _open, _say
from .test_agent_sessions import Upstream, _agent, upstream  # noqa: F401

AUDIENCE = "coaches"
GUIDE = "# Guide\n\n## Tone\n\nAlways answer in plain words.\n"


async def _files_base() -> dict[str, str]:
    """An installation with a restricted `groups` base of mode `files` that nobody has a grant on."""
    from choregos_api.db.models import (
        AssistantInstallation,
        KnowledgeBase,
        KnowledgeDocument,
        Organization,
        User,
    )
    from choregos_api.db.session import session_scope

    ids: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        admin = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        admin.groups = [AUDIENCE]
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        installation = AssistantInstallation(
            org_id=org.id, slug="coach", plugin="p", plugin_version="1", manifest_digest="sha256:0",
            assistant_name="coach", audience_groups=[AUDIENCE],
        )  # fmt: skip
        session.add(installation)
        await session.flush()
        ids["installation"] = installation.id
        base = KnowledgeBase(
            org_id=org.id, slug="coach-guides", name="guides", scope="groups", groups=["nobody-here"],
            restricted=True, serving_mode="files", installation_id=installation.id,
        )  # fmt: skip
        session.add(base)
        await session.flush()
        document = KnowledgeDocument(
            org_id=org.id, knowledge_base_id=base.id, filename="guide.md", title="Guide",
            mime="text/markdown", size_bytes=1, digest="sha256:guide", markdown=GUIDE, status="ready",
            source_version="v3",
        )  # fmt: skip
        session.add(document)
        await session.flush()
        ids["document"] = document.id
    return ids


async def test_a_files_base_is_listed_and_read_whole_and_a_restricted_one_is_served_to_the_assistant(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    ids = await _files_base()
    await _agent(client)
    upstream.model.call("knowledge_list", {}).call(
        "knowledge_get", {"base": "coach-guides", "document_id": ids["document"]}
    ).call("knowledge_search", {"query": "plain words"}).say("ok")
    conversation = await _open("varga", admin, installation_id=ids["installation"])
    assert (await _say(client, conversation, "tone?")).status_code == 200
    (_, first) = upstream.model.requests[0]
    assert {t["function"]["name"] for t in first["tools"]} == {"knowledge_list", "knowledge_get"}, (
        "a files base has no index to search"
    )
    (_, last) = upstream.model.requests[-1]
    listed, read, searched = [m["content"] for m in last["messages"] if m["role"] == "tool"]
    assert f'document_id="{ids["document"]}"' in listed and 'path="guide.md"' in listed
    assert 'version="v3"' in listed
    assert "Always answer in plain words." in read and 'version="v3"' in read
    assert "no tool named `knowledge_search`" in searched
