"""Rapport A/B de la mémoire : elle doit payer, et on refuse de conclure trop vite (S10-06)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient


async def _project(client: AsyncClient, slug: str, *, memory: bool) -> str:
    payload = {
        "slug": slug,
        "name": slug,
        "config": {
            "slug": slug,
            "org": "varga",
            "repo": {"url": f"https://github.com/varga/{slug}.git", "default_branch": "main"},
        },
    }
    response = await client.post("/api/v1/orgs/varga/projects", json=payload)
    assert response.status_code == 201, response.text
    project_id = str(response.json()["id"])

    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row)
        model.memory.enabled = memory
        row.json_doc = model.model_dump(mode="json", exclude_none=True)
    return project_id


async def _tickets(project_id: str, *, count: int, attempts: int, cost: float) -> None:
    """`count` tickets fermés, chacun avec son implémentation en `attempts` tentative(s)."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        for index in range(count):
            item = WorkItem(
                project_id=project_id,
                tracker_key=f"varga/x#{index}",
                title=f"ticket {index}",
                state="deployed_prod",
                closed_at=utcnow() - timedelta(days=3),
            )
            session.add(item)
            await session.flush()
            for attempt in range(1, attempts + 1):
                session.add(
                    Run(
                        work_item_id=item.id,
                        project_id=project_id,
                        stage_role="implement",
                        attempt=attempt,
                        status="succeeded",
                        cost_usd=cost / attempts,
                    )
                )


async def test_sans_assez_de_tickets_le_rapport_refuse_de_conclure(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _tickets(project["id"], count=2, attempts=1, cost=1.0)
    report = (await client.get("/api/v1/orgs/varga/memory/ab-report")).json()
    assert report["verdict"] == "échantillon insuffisant"
    assert "10 de chaque côté" in report["detail"] or "il en faut 10" in report["detail"]


async def test_la_memoire_paie_quand_elle_ameliore_le_premier_passage(
    client: AsyncClient, admin: str
) -> None:
    avec = await _project(client, "avec-memoire", memory=True)
    sans = await _project(client, "sans-memoire", memory=False)
    await _tickets(avec, count=12, attempts=1, cost=1.0)
    await _tickets(sans, count=12, attempts=2, cost=2.0)

    report = (await client.get("/api/v1/orgs/varga/memory/ab-report")).json()

    assert report["with_memory"]["projects"] == ["avec-memoire"]
    assert report["without_memory"]["projects"] == ["sans-memoire"]
    assert report["with_memory"]["first_pass_merge_rate"] == 1.0
    assert report["without_memory"]["first_pass_merge_rate"] == 0.0
    assert report["verdict"] == "la mémoire paie"
    assert "premier passage" in report["detail"]


async def test_la_memoire_ne_paie_pas_quand_elle_coute_plus_cher_sans_gain(
    client: AsyncClient, admin: str
) -> None:
    avec = await _project(client, "avec-memoire", memory=True)
    sans = await _project(client, "sans-memoire", memory=False)
    await _tickets(avec, count=12, attempts=1, cost=6.0)
    await _tickets(sans, count=12, attempts=1, cost=2.0)

    report = (await client.get("/api/v1/orgs/varga/memory/ab-report")).json()

    assert report["verdict"] == "la mémoire ne paie pas", report["detail"]
    assert report["with_memory"]["cost_per_ticket_usd"] == 6.0


async def test_une_organisation_inconnue_ne_pretend_rien(client: AsyncClient, admin: str) -> None:
    report = await client.get("/api/v1/orgs/inconnue/memory/ab-report")
    assert report.status_code in {200, 403}
    if report.status_code == 200:
        assert report.json()["verdict"] == "organisation inconnue"


async def test_la_fenetre_est_reglable(client: AsyncClient, admin: str) -> None:
    avec = await _project(client, "avec-memoire", memory=True)
    await _tickets(avec, count=3, attempts=1, cost=1.0)
    report = (await client.get("/api/v1/orgs/varga/memory/ab-report", params={"weeks": 1})).json()
    assert report["weeks"] == 1
    assert report["with_memory"]["tickets"] == 3, "trois jours, c'est dans la semaine"
