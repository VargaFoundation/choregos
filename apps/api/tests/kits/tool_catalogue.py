"""Tool catalogue helpers: a run that calls catalogue tools, and the provider call it intercepts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

#: A catalogue with one HTTP tool. `run_with_catalogue` reads this module global at call time, so a
#: test that needs another entry patches `kits.tool_catalogue.CATALOGUE`.
CATALOGUE = """
outils:
  - name: recherche_profils
    description: Searches profiles.
    provider: annuaire
    input_schema: { type: object, required: [metier], properties: { metier: { type: string } } }
    http:
      method: GET
      url: https://api.annuaire.example/v1/search
      query: { q: "{{ metier }}" }
      headers: { Authorization: "Bearer {{ credential }}" }
    credential_env: ANNUAIRE_API_KEY
    price_eur: 0.02
"""

MCP_CATALOGUE = """
outils:
  - name: rechercher_entreprise
    description: Looks a company up at a third-party provider.
    provider: annuaire-externe
    groups: [rh]
    input_schema: { type: object, required: [siren], properties: { siren: { type: string } } }
    mcp:
      url: https://mcp.fournisseur.example/mcp
      tool: company_lookup
      headers: { Authorization: "Bearer {{ credential }}" }
    credential_env: FOURNISSEUR_MCP_KEY
    read_only: true
    price_eur: 0.05
"""


def intercept_host(
    monkeypatch: pytest.MonkeyPatch, prefix: str, response: httpx.Response, seen: list[Any]
) -> None:
    """Like `intercept`, but for any host."""
    original = httpx.AsyncClient.request

    async def fake(self: Any, *args: Any, **kwargs: Any) -> httpx.Response:
        url = str(args[1]) if len(args) > 1 else str(kwargs.get("url", ""))
        if not url.startswith(prefix):
            return await original(self, *args, **kwargs)  # type: ignore[no-any-return]
        seen.append(httpx.Request(args[0], url, headers=kwargs.get("headers"), json=kwargs.get("json")))
        return response

    monkeypatch.setattr(httpx.AsyncClient, "request", fake)


def intercept(monkeypatch: pytest.MonkeyPatch, response: httpx.Response, seen: list[Any]) -> None:
    """Replace the call to the PROVIDER, and only it.

    The test client is an `httpx.AsyncClient` too: patching `request` without telling the host apart
    diverted the test's own calls to the API itself, which then answered with the provider's response.
    The first symptom was a `KeyError: 'tools'` on a 200 response, a perfect lie.
    """
    original = httpx.AsyncClient.request

    async def fake(self: Any, *args: Any, **kwargs: Any) -> httpx.Response:
        url = str(args[1]) if len(args) > 1 else str(kwargs.get("url", ""))
        if not url.startswith("https://api.annuaire.example"):
            return await original(self, *args, **kwargs)  # type: ignore[no-any-return]
        seen.append(httpx.Request(args[0], url, params=kwargs.get("params"), headers=kwargs.get("headers")))
        return response

    monkeypatch.setattr(httpx.AsyncClient, "request", fake)


async def run_with_catalogue(
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tools: str = "recherche_profils",
) -> tuple[str, dict[str, str]]:
    from choregos_api import catalogue as catalogue_service
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    file = tmp_path / "catalogue.yaml"
    file.write_text(CATALOGUE, encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(file))
    monkeypatch.setenv("ANNUAIRE_API_KEY", "cle-du-coffre")
    catalogue_service.vider_cache()

    async with session_scope() as session:
        row = await session.get(Project, project["id"])
        assert row is not None
        config = dict(row.config or {})
        config["tools"] = [t for t in tools.split(",") if t]
        row.config = config
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#9", title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            id="run-outils-1",
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="custom",
            transition_id="t-sourcing",
            status="running",
        )
        session.add(run)

    token = mint_run_token(
        "run-outils-1", project_slug="billing-api", work_item_key="varga/x#9", ttl_minutes=30
    )
    return "run-outils-1", {"Authorization": f"Bearer {token}"}


async def project_with_mcp(
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    groups: list[str],
) -> tuple[str, dict[str, str]]:
    from choregos_api import catalogue as catalogue_service
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    file = tmp_path / "catalogue.yaml"
    file.write_text(MCP_CATALOGUE, encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(file))
    monkeypatch.setenv("FOURNISSEUR_MCP_KEY", "cle-du-fournisseur")
    catalogue_service.vider_cache()

    async with session_scope() as session:
        row = await session.get(Project, project["id"])
        assert row is not None
        config = dict(row.config or {})
        config["tools"] = ["rechercher_entreprise"]
        config["groups"] = groups
        row.config = config
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#11", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-mcp-1",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="custom",
                status="running",
            )
        )

    token = mint_run_token(
        "run-mcp-1", project_slug="billing-api", work_item_key="varga/x#11", ttl_minutes=30
    )
    return "run-mcp-1", {"Authorization": f"Bearer {token}"}
