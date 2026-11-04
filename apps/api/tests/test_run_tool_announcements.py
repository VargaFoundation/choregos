"""What the run's tool list says of each tool, and the door a call came through (S57-05, ADR 0122).

`getRunTools` announces, beside MCP's `name`, `description` and `inputSchema`, MCP's own
`annotations.readOnlyHint`, the platform's `x-choregos-access` (`read`, `write`, `approval`) and the
`outputSchema` an operation declares: what the generated client of a script reads (S57-08).
`callRunTool` reads `X-Choregos-Tool-Origin` and `X-Choregos-Script` and records them on
`choregos.tool.called`; nothing else reads them.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from .kits.extension_tools import run_extension_tool
from .kits.tool_catalogue import intercept, run_with_catalogue
from .test_broker import ORG, TOOLS, _prepare

STOCK_SCHEMA = {"type": "object", "required": ["quantity"], "properties": {"quantity": {"type": "integer"}}}
DIGEST = "0f" * 32


@pytest.fixture
def supplier(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The broker's supplier server, whose `stock` declares the shape of its answer."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    tools = [dict(o) for o in TOOLS]
    for tool in tools:
        if tool["name"] == "stock":
            tool["outputSchema"] = STOCK_SCHEMA
    server = FakeMcpServer(tools=tools, token="cle-fournisseur")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_CLE_FOURNISSEUR", "cle-fournisseur")
    yield server


async def _listed(client: AsyncClient, base: str, headers: dict[str, str]) -> dict[str, dict[str, Any]]:
    listing = await client.get(base, headers=headers)
    assert listing.status_code == 200, listing.text
    return {tool["name"]: tool for tool in listing.json()["tools"]}


def _access(tool: dict[str, Any]) -> tuple[bool, str]:
    return tool["annotations"]["readOnlyHint"], tool["x-choregos-access"]


# ───────────────────────── getRunTools ─────────────────────────


async def test_a_broker_read_a_write_and_an_approval_announce_what_a_call_does(
    client: AsyncClient, project: dict[str, Any], supplier: Any
) -> None:
    base, headers = await _prepare(client, project, ["*"])
    tools = await _listed(client, base, headers)
    assert _access(tools["fournisseur__suivi_commande"]) == (True, "read")
    assert _access(tools["fournisseur__commander_poste"]) == (False, "approval"), "a call proposes"
    opened = await client.patch(
        f"{ORG}/connectors/fournisseur/operations/commander_poste", json={"policy": "allowed"}
    )
    assert opened.status_code == 200, opened.text
    tools = await _listed(client, base, headers)
    assert _access(tools["fournisseur__commander_poste"]) == (False, "write"), "a governed action"
    assert _access(tools["fournisseur__suivi_commande"]) == (True, "read")


async def test_the_output_schema_is_announced_when_the_operation_declares_one(
    client: AsyncClient, project: dict[str, Any], supplier: Any
) -> None:
    base, headers = await _prepare(client, project, ["*"])
    tools = await _listed(client, base, headers)
    assert tools["fournisseur__stock"]["outputSchema"] == STOCK_SCHEMA
    assert "outputSchema" not in tools["fournisseur__suivi_commande"], "none declared, none announced"


async def test_a_read_under_approval_is_an_approval_not_a_read(
    client: AsyncClient, project: dict[str, Any], supplier: Any
) -> None:
    """`readOnlyHint` says a call does not change anything: a proposal lands in the inbox."""
    base, headers = await _prepare(client, project, ["*"])
    held = await client.patch(f"{ORG}/connectors/fournisseur/operations/stock", json={"policy": "approval"})
    assert held.status_code == 200, held.text
    tools = await _listed(client, base, headers)
    assert _access(tools["fournisseur__stock"]) == (False, "approval")


async def test_a_catalogue_tool_and_web_fetch_are_reads(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalogue tool that may write is not listed (S47-12): what is listed reads, as the web tools do."""
    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch)
    tools = await _listed(client, f"/api/v1/internal/runs/{run_id}/tools", headers)
    assert set(tools) >= {"recherche_profils", "web_fetch"}
    for name in tools:
        assert _access(tools[name]) == (True, "read"), name
    assert tools["recherche_profils"]["inputSchema"]["required"] == ["metier"]


def _extension(monkeypatch: pytest.MonkeyPatch, listed: list[dict[str, Any]]) -> None:
    from choregos_api import extensions

    async def list_tools(session: Any, run: Any, project: Any) -> list[dict[str, Any]]:
        return [dict(tool) for tool in listed]

    async def call_tool(session: Any, run: Any, project: Any, name: str, arguments: Any) -> tuple[int, Any]:
        return 200, {}

    monkeypatch.setitem(
        extensions._TOOL_PROVIDERS, "announcements", extensions.ToolProvider(list_tools, call_tool)
    )


async def test_an_extension_tool_says_what_it_does_or_is_a_write(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _extension(
        monkeypatch,
        [
            {"name": "lookup", "description": "Looks up.", "inputSchema": {"type": "object"},
             "annotations": {"readOnlyHint": True, "title": "Lookup"}},
            {"name": "update", "description": "Updates.", "inputSchema": {"type": "object"},
             "result_max_bytes": 4096},
            {"name": "propose", "description": "Proposes.", "inputSchema": {"type": "object"},
             "annotations": {"readOnlyHint": True}, "x-choregos-access": "approval"},
        ],
    )  # fmt: skip
    run_id, headers = await run_extension_tool(project["id"])
    tools = await _listed(client, f"/api/v1/internal/runs/{run_id}/tools", headers)
    assert _access(tools["lookup"]) == (True, "read"), "annotated `readOnlyHint: true`"
    assert tools["lookup"]["annotations"]["title"] == "Lookup", "the extension's own annotations stay"
    assert _access(tools["update"]) == (False, "write"), "not said to only read: MCP's default, a write"
    assert "result_max_bytes" not in tools["update"]
    assert _access(tools["propose"]) == (False, "approval"), "declared; `readOnlyHint` follows the access"


async def test_an_extension_tool_declaring_another_access_is_refused_when_listed(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _extension(
        monkeypatch,
        [
            {
                "name": "odd",
                "description": "Odd.",
                "inputSchema": {"type": "object"},
                "x-choregos-access": "admin",
            }
        ],
    )
    run_id, headers = await run_extension_tool(project["id"])
    listing = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
    assert listing.status_code == 500, listing.text
    assert "x-choregos-access='admin'" in listing.json()["detail"]
    called = await client.post(f"/api/v1/internal/runs/{run_id}/tools/odd", headers=headers, json={})
    assert called.status_code == 500, "refused the same way when called"


# ───────────────────────── callRunTool: the origin headers ─────────────────────────


async def _call(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: dict[str, str],
) -> tuple[httpx.Response, list[dict[str, Any]]]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch)
    intercept(
        monkeypatch,
        httpx.Response(
            200, json={"profiles": []}, request=httpx.Request("GET", "https://api.annuaire.example/v1")
        ),
        [],
    )
    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils",
        headers={**headers, **extra},
        json={"metier": "x"},
    )
    async with session_scope() as session:
        events = (
            (await session.execute(select(Event).where(Event.type == "choregos.tool.called"))).scalars().all()
        )
    return response, [dict(e.payload) for e in events if e.subject == run_id]


@pytest.mark.parametrize(
    "extra",
    [
        {"X-Choregos-Tool-Origin": "agent"},
        {"X-Choregos-Tool-Origin": "Script"},
        {"X-Choregos-Tool-Origin": ""},
        {"X-Choregos-Script": "0F" * 32},
        {"X-Choregos-Script": "0f" * 31},
        {"X-Choregos-Tool-Origin": "script", "X-Choregos-Script": "g" * 64},
    ],
)
async def test_an_origin_header_outside_the_contract_is_a_400_and_nothing_is_booked(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: dict[str, str],
) -> None:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    response, events = await _call(client, project, tmp_path, monkeypatch, extra)
    assert response.status_code == 400, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["status"] == 400
    assert problem["errors"][0]["code"] in {"tool_origin_refused", "script_digest_refused"}
    assert events == []
    async with session_scope() as session:
        assert (await session.execute(select(CostLedger))).scalars().all() == []


@pytest.mark.parametrize(
    ("extra", "origin", "digest"),
    [
        ({}, "model", None),
        ({"X-Choregos-Tool-Origin": "model"}, "model", None),
        ({"X-Choregos-Tool-Origin": "script", "X-Choregos-Script": DIGEST}, "script", DIGEST),
        ({"x-choregos-tool-origin": "job"}, "job", None),
    ],
)
async def test_a_good_origin_lands_in_the_event(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: dict[str, str],
    origin: str,
    digest: str | None,
) -> None:
    response, events = await _call(client, project, tmp_path, monkeypatch, extra)
    assert response.status_code == 200, response.text
    (event,) = events
    assert event["origin"] == origin
    assert event.get("script_sha256") == digest
    assert ("script_sha256" in event) is (digest is not None), "no digest, no key"
    assert event["response_bytes"] > 0, "S57-01's size stays"


def test_the_contract_names_the_origins_the_api_reads() -> None:
    import choregos_contracts as contracts
    from choregos_api.routers import internal

    operation = contracts.load_openapi()["paths"]["/internal/runs/{id}/tools/{name}"]["post"]
    headers = {p["name"]: p["schema"] for p in operation["parameters"]}
    assert tuple(headers[internal.TOOL_ORIGIN_HEADER]["enum"]) == internal.TOOL_ORIGINS
    assert headers[internal.SCRIPT_HEADER]["pattern"] == "^[0-9a-f]{64}$"
    entry = contracts.load_openapi()["components"]["schemas"]["RunTool"]["properties"]
    assert entry[internal.ACCESS_KEY]["enum"] == ["read", "write", "approval"]
