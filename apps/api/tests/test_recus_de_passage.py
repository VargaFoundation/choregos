"""Les reçus de passage entre étapes (S25-04).

Une étape produit une spec ; la suivante la lit. Rien ne disait QUELLE révision elle avait lue : une
sortie réécrite entre-temps passait sans trace. Chaque sortie rangée laisse un reçu « produite » sous
son empreinte, chaque lecture un reçu « lue » sous l'empreinte de ce qui a été lu, et la route
publique dit ce que la sortie vaut aujourd'hui.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

SPEC = "# Spec\n\nDeduct credit notes from the total.\n"
SPEC_REVUE = "# Spec\n\nDeduct credit notes, then round half to even.\n"


async def _ticket(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    cree = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Avoirs", "start": False}
    )
    assert cree.status_code == 201, cree.text
    return dict(cree.json())


async def _run(ticket: dict[str, Any], project: dict[str, Any], run_id: str, role: str) -> dict[str, str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=ticket["id"],
                project_id=project["id"],
                stage_role=role,
                status="running",
            )
        )
    jeton = mint_run_token(
        run_id, project_slug=project["slug"], work_item_key=ticket["tracker_key"], ttl_minutes=5
    )
    return {"Authorization": f"Bearer {jeton}"}


async def _produire(client: AsyncClient, run_id: str, headers: dict[str, str], spec: str) -> None:
    resultat = {
        "schema": "choregos/StageResult/v1",
        "status": "done",
        "summary": "spec written",
        "outputs": {"spec_markdown": spec},
    }
    posted = await client.post(f"/api/v1/internal/runs/{run_id}/result", headers=headers, json=resultat)
    assert posted.status_code == 200, posted.text


async def _passages(client: AsyncClient, ticket: dict[str, Any]) -> dict[str, Any]:
    lu = await client.get(f"/api/v1/work-items/{ticket['id']}/hand-offs")
    assert lu.status_code == 200, lu.text
    return {passage["output"]: passage for passage in lu.json()}


async def test_l_etape_suivante_lit_la_revision_exacte_produite(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _ticket(client, project)
    raffiner = await _run(ticket, project, "run-raffiner", "refine")
    await _produire(client, "run-raffiner", raffiner, SPEC)

    implementer = await _run(ticket, project, "run-implementer", "implement")
    lu = await client.get("/api/v1/internal/runs/run-implementer/ticket", headers=implementer)
    assert lu.json()["spec_markdown"] == SPEC
    # Lire deux fois ne fait qu'un reçu ; ce qui n'existe pas (le plan) n'en fait aucun.
    await client.get("/api/v1/internal/runs/run-implementer/ticket", headers=implementer)

    spec = (await _passages(client, ticket))["spec_markdown"]
    assert [(e["kind"], e["run_id"], e["stage"]) for e in spec["events"]] == [
        ("produced", "run-raffiner", "refine"),
        ("read", "run-implementer", "implement"),
    ]
    produite, lue = spec["events"]
    assert produite["digest"].startswith("sha256:") and len(produite["digest"]) == 71
    assert lue["digest"] == produite["digest"], "la révision lue est la révision produite"
    assert spec["current_digest"] == produite["digest"]


async def test_une_sortie_modifiee_apres_coup_change_d_empreinte(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Réécrite à la main après son reçu, la spec n'a plus l'empreinte de son dernier reçu « produite » —
    et l'étape qui la lit ensuite lit une révision qu'aucune étape n'a produite."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    ticket = await _ticket(client, project)
    raffiner = await _run(ticket, project, "run-r1", "refine")
    await _produire(client, "run-r1", raffiner, SPEC)
    async with session_scope() as session:
        item = await session.get(WorkItem, ticket["id"])
        assert item is not None
        item.documents = {**(item.documents or {}), "spec_markdown": SPEC_REVUE}

    implementer = await _run(ticket, project, "run-i1", "implement")
    await client.get("/api/v1/internal/runs/run-i1/ticket", headers=implementer)

    spec = (await _passages(client, ticket))["spec_markdown"]
    produite, lue = spec["events"]
    assert spec["current_digest"] != produite["digest"], "modifiée après coup : ça se voit"
    assert lue["digest"] == spec["current_digest"] != produite["digest"]


async def test_un_recu_ne_change_plus_une_nouvelle_revision_en_fait_un_autre(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _ticket(client, project)
    premier = await _run(ticket, project, "run-a1", "refine")
    await _produire(client, "run-a1", premier, SPEC)
    # Le même résultat reposté : déjà consigné, aucun second reçu.
    await _produire(client, "run-a1", premier, SPEC)
    reprise = await _run(ticket, project, "run-a2", "refine")
    await _produire(client, "run-a2", reprise, SPEC_REVUE)

    spec = (await _passages(client, ticket))["spec_markdown"]
    assert [(e["kind"], e["run_id"]) for e in spec["events"]] == [
        ("produced", "run-a1"),
        ("produced", "run-a2"),
    ]
    premiere, seconde = spec["events"]
    assert premiere["digest"] != seconde["digest"]
    assert spec["current_digest"] == seconde["digest"]


async def test_les_recus_d_un_ticket_se_lisent_avec_le_droit_de_lire_le_projet(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _ticket(client, project)
    assert await _passages(client, ticket) == {}
    absent = await client.get("/api/v1/work-items/nope/hand-offs")
    assert absent.status_code == 404


def test_l_empreinte_ne_depend_pas_de_l_ordre_des_cles() -> None:
    from choregos_api.services.recus import empreinte, lu_par_le_prompt

    assert empreinte({"a": 1, "b": [1, 2]}) == empreinte({"b": [1, 2], "a": 1})
    assert empreinte("x") != empreinte("x ")
    # Le prompt n'embarque la spec que s'il la cite ; une entrée déclarée est lue, présente ou non vide.
    documents = {"spec_markdown": SPEC, "plan_markdown": "# Plan", "profils": ["a", "b"], "vide": ""}
    assert lu_par_le_prompt(documents, ["profils", "vide"], f"Read this:\n{SPEC}") == {
        "profils": ["a", "b"],
        "spec_markdown": SPEC,
    }
