# SPDX-License-Identifier: Apache-2.0
"""Helpers shared by the tests of the plugin: packages, reports, projects and runs."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import httpx
from httpx import AsyncClient

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CORE_REF = FIXTURES / "core-ref"
IT4IT = FIXTURES / "it4it"


async def connecter(http: AsyncClient, email: str, *, reauth: bool = False) -> None:
    """Connexion de développement : `?as=email` ouvre une session sans IdP."""
    parametres = {"as": email, **({"reauth": "1"} if reauth else {})}
    debut = await http.get("/api/v1/auth/login", params=parametres)
    assert debut.status_code == 307, debut.text
    assert (await http.get(debut.headers["location"])).status_code == 307


async def creer_projet(http: AsyncClient, slug: str) -> dict[str, Any]:
    reponse = await http.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": slug,
            "name": slug,
            "config": {"slug": slug, "org": "varga", "repo": {"url": f"https://github.com/varga/{slug}.git"}},
        },
    )
    assert reponse.status_code == 201, reponse.text
    return dict(reponse.json())


def paquet(racine: pathlib.Path = CORE_REF) -> dict[str, str]:
    return {
        chemin.relative_to(racine).as_posix(): chemin.read_text(encoding="utf-8")
        for chemin in sorted(racine.rglob("*.yaml"))
    }


def rapport(*lignes: dict[str, Any], fin: dict[str, Any] | None = None) -> str:
    corps = [json.dumps(ligne) for ligne in lignes]
    corps.append(json.dumps(fin if fin is not None else {"_end": True, "lines": len(lignes)}))
    return "\n".join(corps) + "\n"


def ligne(check: str, scope: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"layer": "os", "check": check, "scope": scope, "status": status, **extra}


async def poster(http: AsyncClient, projet: dict[str, Any], texte: str) -> httpx.Response:
    return await http.post(
        f"/api/v1/projects/{projet['id']}/observations",
        content=texte.encode(),
        headers={"Content-Type": "application/x-ndjson"},
    )


async def objets(projet_id: str, type_: str = "finding") -> dict[str, tuple[int, dict[str, Any]]]:
    """L'état en base, lu sous le greffon : {id: (row_version, propriétés)}."""
    from choregos_api.db.session import session_scope
    from choregos_ontology.service.store import ManagedObject
    from sqlalchemy import select

    async with session_scope() as session:
        du_type = ManagedObject.project_id == projet_id, ManagedObject.object_type == type_
        lignes = await session.execute(select(ManagedObject).where(*du_type))
        return {o.id: (o.row_version, dict(o.properties)) for o in lignes.scalars()}


async def creer_run(projet: dict[str, Any], run_id: str) -> dict[str, str]:
    """Un run du projet, et l'en-tête de son jeton — comme les frappe l'orchestrateur."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    cle = f"varga/{run_id}#1"
    async with session_scope() as session:
        item = WorkItem(project_id=projet["id"], tracker_key=cle, title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(Run(id=run_id, work_item_id=item.id, project_id=projet["id"], stage_role="analyse_infra"))
    jeton = mint_run_token(run_id, project_slug=projet["slug"], work_item_key=cle, ttl_minutes=30)
    return {"Authorization": f"Bearer {jeton}"}


async def appeler_outil(
    http: AsyncClient, run_id: str, entetes: dict[str, str], outil: str, arguments: dict[str, Any]
) -> Any:
    """Un appel d'outil par l'API interne, comme le relaie `choregos-tools` : rend le corps JSON."""
    reponse = await http.post(
        f"/api/v1/internal/runs/{run_id}/tools/{outil}", headers=entetes, json=arguments
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()
