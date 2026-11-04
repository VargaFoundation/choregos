"""Tool catalogue: the agent calls with its run token, the key stays here.

The HR demonstration of 2026-09-23 stopped on "no profile base reachable". The reflex would have
been to put an API key in the agent's pod; these tests check that the platform makes the call in its
place, caps it and counts it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from .kits.tool_catalogue import intercept, intercept_host, project_with_mcp, run_with_catalogue

pytestmark = pytest.mark.asyncio


async def test_the_agent_calls_a_tool_without_ever_seeing_the_key(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch)
    seen: list[Any] = []
    intercept(
        monkeypatch,
        httpx.Response(
            200,
            json={"profiles": [{"name": "A"}, {"name": "B"}]},
            request=httpx.Request("GET", "https://api.annuaire.example/v1/search"),
        ),
        seen,
    )

    listing = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
    assert listing.status_code == 200, listing.text
    # `web_fetch` is the platform's own tool (S40-01f): offered to any run whose network allows a host.
    assert [t["name"] for t in listing.json()["tools"] if t["name"] != "web_fetch"] == ["recherche_profils"]

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils",
        headers=headers,
        json={"metier": "data engineer"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["profiles"][0]["name"] == "A"
    # The key went to the provider, and nowhere else.
    assert seen[0].headers["Authorization"] == "Bearer cle-du-coffre"
    assert "cle-du-coffre" not in response.text


async def test_the_call_is_entered_in_the_cost_ledger(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalogue tool costs on every call. Uncounted, it would be the platform's only spend that
    nobody sees."""
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch)

    intercept(
        monkeypatch,
        httpx.Response(
            200, json={"profiles": []}, request=httpx.Request("GET", "https://api.annuaire.example/v1/search")
        ),
        [],
    )
    await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils", headers=headers, json={"metier": "x"}
    )

    async with session_scope() as session:
        rows = (await session.execute(select(CostLedger).where(CostLedger.run_id == run_id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "tool"
    assert rows[0].provider == "annuaire"
    assert rows[0].cost_eur == pytest.approx(0.02)


async def test_the_event_carries_the_digest_of_the_request_and_the_response(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provenance: the event keeps the SHA-256 of the canonical JSON of what went in and of what
    came back, so a result disputed later can be matched to what the run was served."""
    import hashlib
    import json

    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch)
    intercept(
        monkeypatch,
        httpx.Response(
            200,
            json={"profiles": [{"name": "é"}]},
            request=httpx.Request("GET", "https://api.annuaire.example/v1/search"),
        ),
        [],
    )
    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils",
        headers=headers,
        json={"metier": "x", "a": 1},
    )
    assert response.status_code == 200, response.text

    def digest_of(value: Any) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    async with session_scope() as session:
        events = (
            (await session.execute(select(Event).where(Event.type == "choregos.tool.called"))).scalars().all()
        )
    (event,) = [e for e in events if e.subject == run_id]
    assert event.payload["request_sha256"] == digest_of({"metier": "x", "a": 1})
    assert event.payload["response_sha256"] == digest_of(response.json()["result"])


async def test_a_tool_not_declared_by_the_project_is_not_found(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And it returns 404, not 403: an agent has no business discovering the deployment's catalogue by
    trying names."""
    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch, tools="")
    tools = (await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)).json()["tools"]
    assert [t["name"] for t in tools if t["name"] != "web_fetch"] == []
    refusal = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils", headers=headers, json={"metier": "x"}
    )
    assert refusal.status_code == 404


async def test_the_call_ceiling_bounds_the_bill(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paid tool without a ceiling is a bill without a ceiling: a runaway loop costs as much as it
    runs."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch)
    intercept(
        monkeypatch,
        httpx.Response(
            200, json={"profiles": []}, request=httpx.Request("GET", "https://api.annuaire.example/v1/search")
        ),
        [],
    )

    async with session_scope() as session:
        policy = (
            (
                await session.execute(
                    select(PolicyDef)
                    .where(PolicyDef.project_id == project["id"])
                    .order_by(PolicyDef.version.desc())
                )
            )
            .scalars()
            .first()
        )
        assert policy is not None
        document = dict(policy.json_doc)
        document["budgets"] = {**(document.get("budgets") or {}), "tool_calls_per_run": 2}
        policy.json_doc = document

    call = lambda: client.post(  # noqa: E731
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils", headers=headers, json={"metier": "x"}
    )
    assert (await call()).json()["remaining"] == 1
    assert (await call()).json()["remaining"] == 0
    too_many = await call()
    assert too_many.status_code == 429, too_many.text


async def test_the_settings_screen_sees_the_whole_catalogue(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, admin: str
) -> None:
    """BOTH sets: without the full list, one can neither know what could be opened nor understand why
    a tool is missing for the agent. And never the key."""
    await run_with_catalogue(project, tmp_path, monkeypatch, tools="")

    response = await client.get(f"/api/v1/projects/{project['id']}/tools")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [t["name"] for t in body["tools"]] == ["recherche_profils"]
    assert body["tools"][0]["allowed"] is False, "not declared by this project"
    assert body["tools"][0]["needs_credential"] is True
    assert "ANNUAIRE_API_KEY" not in response.text and "cle-du-coffre" not in response.text
    assert body["allows_all"] is False


async def test_the_run_token_never_leaves_for_a_remote_mcp(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE point of this feature. The run token authenticates the agent to US: giving it to a third
    party would hand it the means to write in the platform (post a result, file a finding, widen a
    scope). The remote receives a key that is valid for it alone."""
    run_id, headers = await project_with_mcp(project, tmp_path, monkeypatch, groups=["rh"])
    run_token = headers["Authorization"].removeprefix("Bearer ")
    seen: list[Any] = []
    intercept_host(
        monkeypatch,
        "https://mcp.fournisseur.example",
        httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "SAS X"}]}},
            request=httpx.Request("POST", "https://mcp.fournisseur.example/mcp"),
        ),
        seen,
    )

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/rechercher_entreprise",
        headers=headers,
        json={"siren": "552100554"},
    )
    assert response.status_code == 200, response.text

    outgoing = seen[0]
    assert outgoing.headers["Authorization"] == "Bearer cle-du-fournisseur"
    assert run_token not in str(outgoing.headers), "the run token left!"
    body = outgoing.read().decode()
    assert run_token not in body, "the run token went out in the body!"
    # And it is the REMOTE name that goes out, not the one the agent knows.
    assert '"name": "company_lookup"' in body or '"name":"company_lookup"' in body


async def test_a_project_outside_the_group_does_not_get_the_tool(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The project declares the tool in `tools`, but it is not in the group. The project's list can be
    changed by its team; the group cannot."""
    run_id, headers = await project_with_mcp(project, tmp_path, monkeypatch, groups=["legal"])

    listing = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
    assert [t["name"] for t in listing.json()["tools"] if t["name"] != "web_fetch"] == []
    refusal = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/rechercher_entreprise",
        headers=headers,
        json={"siren": "1"},
    )
    assert refusal.status_code == 404


async def test_an_argument_outside_the_schema_is_refused_and_costs_nothing(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The schema announced to the agent must be applied, and the refusal must come before the call.

    `input_schema` was decorative: the platform returns it to the agent in the MCP format
    (`GET /internal/runs/{id}/tools`, key `inputSchema`) and never checked it. Two consequences, the
    second being the costlier: a paid provider received a malformed call, and the ledger counted it
    anyway.
    """
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch)
    seen: list[Any] = []
    intercept(
        monkeypatch,
        httpx.Response(
            200, json={"profiles": []}, request=httpx.Request("GET", "https://api.annuaire.example/v1/search")
        ),
        seen,
    )

    # A PRESENT argument of the wrong type. That is the discriminating case, not the missing
    # argument: a missing argument was already refused by the template (`{{ metier }}` has nothing
    # to substitute). An argument of the wrong type passed: `str(value)` accepts anything, and the
    # provider received `q={'sql': '...'}`.
    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils",
        headers=headers,
        json={"metier": {"sql": "1=1"}},
    )
    assert response.status_code == 400, response.text
    assert "metier" in response.text, f"the refusal must name the faulty argument: {response.text}"

    assert not seen, "the provider was called despite a refused argument"
    async with session_scope() as session:
        rows = (await session.execute(select(CostLedger).where(CostLedger.run_id == run_id))).scalars().all()
    assert not rows, "a refused call was entered in the cost ledger"
