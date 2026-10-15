"""Drafting an agent from a description (S39-05): one capped gateway call, a proposal that is never
saved, restricted to what the organisation has."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from choregos_api.services import agent_drafts
from choregos_api.services.agent_drafts import DraftContext, DraftingError, GatewayAgentDrafter
from httpx import AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"
REQUEST = {
    "display_name": "Release notes writer",
    "description": "Writes release notes from the pull requests merged since the last tag.",
}
SKILL_MD = (
    "---\nname: release-style\ndescription: How we write release notes.\n---\n\n# Style\n\n1. Be short.\n"
)


@pytest.fixture(autouse=True)
def _fresh_window() -> None:
    agent_drafts.limiter._fenetres.clear()


class Scripted:
    """A drafter that answers what the test says and remembers what it was given."""

    model = "scripted/drafter"

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer, self.instructions, self.context = answer, "", DraftContext()

    async def draft(self, instructions: str, request: Any, context: DraftContext) -> dict[str, Any]:
        self.instructions, self.context = instructions, context
        return self.answer


def _script(monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any]) -> Scripted:
    drafter = Scripted(answer)
    monkeypatch.setattr(agent_drafts, "get_drafter", lambda settings=None: drafter)
    return drafter


async def test_a_draft_is_proposed_not_saved(client: AsyncClient, admin: str) -> None:
    await client.post(f"{ORG}/skills", json={"files": {"SKILL.md": SKILL_MD}})
    before = (await client.get(f"{ORG}/agents")).json()
    response = await client.post(f"{ORG}/agent-drafts", json=REQUEST)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["spec"]["instructions"].strip()
    assert draft["slug"] == "release-notes-writer" and draft["sample"]["title"]
    assert draft["spec"]["skills"] == [{"slug": "release-style", "version": None}]
    assert 1 <= len(draft["spec"]["evals"]) <= 2 and draft["model"] == "fake/agent-drafter"
    assert (await client.get(f"{ORG}/agents")).json() == before, "nothing is created"
    audit = (await client.get("/api/v1/audit")).json()["items"]
    (entry,) = [e for e in audit if e["action"] == "agent.draft"]
    assert entry["payload"]["description_chars"] == len(REQUEST["description"])
    assert REQUEST["description"] not in json.dumps(entry), "the description is not in the audit"


async def test_suggestions_are_restricted_to_what_exists(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await client.post(f"{ORG}/skills", json={"files": {"SKILL.md": SKILL_MD}})
    await client.post(f"{ORG}/knowledge", json={"slug": "style-guide", "name": "Style guide"})
    drafter = _script(
        monkeypatch,
        {
            "instructions": "1. Write.\n## Output\nNotes.",
            "skills": ["release-style", "ghost"],
            "knowledge": ["style-guide", "ghost-kb"],
            "mcp_servers": [{"connector": "ghost-connector", "tools": ["x"]}],
            "limits": {"max_turns": 5000, "max_minutes": 30},
            "budget": {"run_usd": 100},
        },
    )
    draft = (await client.post(f"{ORG}/agent-drafts", json=REQUEST)).json()
    assert [s["slug"] for s in draft["spec"]["skills"]] == ["release-style"]
    assert [k["slug"] for k in draft["spec"]["knowledge"]] == ["style-guide"]
    assert draft["spec"]["mcp_servers"] == []
    notes = " ".join(draft["notes"])
    assert "`ghost`" in notes and "`ghost-kb`" in notes and "`ghost-connector`" in notes
    assert draft["spec"]["limits"]["max_turns"] == 100 and draft["spec"]["budget"]["run_usd"] == 5.0
    assert [s for s, _ in drafter.context.skills] == ["release-style"]
    assert draft["model"] == "scripted/drafter"


async def test_instructions_with_an_unknown_variable_are_refused_in_notes(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(monkeypatch, {"instructions": "Leak {{ secrets }} now."})
    draft = (await client.post(f"{ORG}/agent-drafts", json=REQUEST)).json()
    assert draft["spec"]["instructions"] == ""
    assert any("do not render" in n for n in draft["notes"])


async def test_only_an_org_admin_drafts(client: AsyncClient, admin: str) -> None:
    await client.post(f"{ORG}/members", json={"email": "dev@varga.dev", "role": "developer"})
    await login(client, "dev@varga.dev")
    assert (await client.post(f"{ORG}/agent-drafts", json=REQUEST)).status_code == 403


async def test_drafting_is_rate_limited(client: AsyncClient, admin: str) -> None:
    for _ in range(10):
        assert (await client.post(f"{ORG}/agent-drafts", json=REQUEST)).status_code == 200
    eleventh = await client.post(f"{ORG}/agent-drafts", json=REQUEST)
    assert eleventh.status_code == 429 and int(eleventh.headers["Retry-After"]) >= 1


async def test_the_description_has_a_floor(client: AsyncClient, admin: str) -> None:
    short = await client.post(f"{ORG}/agent-drafts", json={"display_name": "X", "description": "too short"})
    assert short.status_code == 422


async def test_a_gateway_failure_is_a_502(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down:
        model = "down"

        async def draft(self, *args: Any) -> dict[str, Any]:
            raise DraftingError("the gateway answered 500: boom")

    monkeypatch.setattr(agent_drafts, "get_drafter", lambda settings=None: Down())
    failed = await client.post(f"{ORG}/agent-drafts", json=REQUEST)
    assert failed.status_code == 502 and "boom" in failed.text


async def test_gateway_drafter_sends_a_capped_request() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"instructions": "x"}'}}]})

    drafter = GatewayAgentDrafter(
        "http://gateway", "k", "platform/standard", transport=httpx.MockTransport(handler)
    )
    request = agent_drafts.AgentDraftRequest.model_validate(REQUEST)
    answer = await drafter.draft("SYSTEM", request, DraftContext(skills=[("release-style", "style")]))
    assert answer == {"instructions": "x"}
    (body,) = seen
    assert body["max_tokens"] == 2500 and body["response_format"] == {"type": "json_object"}
    assert body["metadata"] == {"choregos_agent": "agent-drafter"} and body["model"] == "platform/standard"
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert "release-style" in body["messages"][1]["content"]
    assert REQUEST["description"] in body["messages"][1]["content"]

    for content in ("not json", "[1]"):
        bad = GatewayAgentDrafter(
            "http://gateway",
            "k",
            "m",
            transport=httpx.MockTransport(
                lambda r, c=content: httpx.Response(200, json={"choices": [{"message": {"content": c}}]})
            ),
        )
        with pytest.raises(DraftingError):
            await bad.draft("S", request, DraftContext())
    refused = GatewayAgentDrafter(
        "http://gateway", "k", "m", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="no"))
    )
    with pytest.raises(DraftingError, match="500"):
        await refused.draft("S", request, DraftContext())


async def test_the_org_can_override_the_drafter(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    drafter = _script(monkeypatch, {"instructions": "1. Go."})
    await client.post(f"{ORG}/agent-drafts", json=REQUEST)
    from choregos_core.catalogue_d_agents import entree

    assert drafter.instructions == entree("agent-drafter").document["spec"]["instructions"]  # type: ignore[union-attr]
    assert (await client.post(f"{ORG}/agent-catalogue/agent-drafter/install")).status_code == 201
    mine = {"instructions": "Our house drafting rules.", "limits": {"max_turns": 1}}
    published = await client.post(f"{ORG}/agents/agent-drafter/versions", json=mine)
    assert published.status_code == 201, published.text
    await client.post(f"{ORG}/agent-drafts", json=REQUEST)
    assert drafter.instructions == "Our house drafting rules."


async def test_reserved_slug_new(client: AsyncClient, admin: str) -> None:
    refused = await client.post(f"{ORG}/agents", json={"slug": "new", "display_name": "New"})
    assert refused.status_code == 422 and "reserved" in refused.text
    assert (
        await client.post(f"{ORG}/agents", json={"slug": "new-one", "display_name": "N"})
    ).status_code == 201


def test_slug_from_name() -> None:
    assert agent_drafts.slug_from_name("Release notes writer!") == "release-notes-writer"
    assert agent_drafts.slug_from_name("Zo\u00eb's agent") == "zoes-agent"
    assert agent_drafts.slug_from_name("new") == "new-agent"
    assert agent_drafts.slug_from_name("42") == "new-agent"
