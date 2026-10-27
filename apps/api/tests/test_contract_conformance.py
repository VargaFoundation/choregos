"""L'application implémente exactement le contrat : mêmes chemins, mêmes opérations."""

from __future__ import annotations

from typing import Any

import choregos_contracts as contracts

PREFIX = "/api/v1"

# Contract-first (ADR 0001): operations the contract declares before the API serves them. Each entry
# names the story that implements it; an entry the API does serve must be removed from here.
CONTRACT_ONLY: dict[str, str] = {}
CONTRACT_ONLY_PATHS: set[str] = set()


def _app_paths(spec: dict[str, Any]) -> set[str]:
    out = set()
    for path in spec["paths"]:
        if path.startswith(PREFIX):
            out.add(path.removeprefix(PREFIX))
        else:
            out.add(path)
    return out


async def test_paths_match_contract(app: Any) -> None:
    spec = app.openapi()
    declared = set(contracts.load_openapi()["paths"])
    implemented = _app_paths(spec)
    missing = declared - implemented - CONTRACT_ONLY_PATHS
    extra = implemented - declared
    assert not missing, f"chemins du contrat non implémentés : {sorted(missing)}"
    assert not extra, f"chemins implémentés hors contrat : {sorted(extra)}"


async def test_operation_ids_match_contract(app: Any) -> None:
    spec = app.openapi()
    declared = {
        op["operationId"]
        for path in contracts.load_openapi()["paths"].values()
        for verb, op in path.items()
        if verb in {"get", "post", "put", "patch", "delete"}
    }
    implemented = {
        op["operationId"]
        for path in spec["paths"].values()
        for verb, op in path.items()
        if verb in {"get", "post", "put", "patch", "delete"} and "operationId" in op
    }
    assert declared - set(CONTRACT_ONLY) == implemented, (
        f"manquants : {sorted(declared - implemented - set(CONTRACT_ONLY))} ; "
        f"en trop : {sorted(implemented - declared)}"
    )
    served = sorted(set(CONTRACT_ONLY) & implemented)
    assert not served, f"served now, remove from CONTRACT_ONLY: {served}"


async def test_health_endpoints(client: Any) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200
