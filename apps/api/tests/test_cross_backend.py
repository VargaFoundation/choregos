"""Mesure de la revue croisée : un autre backend attrape-t-il davantage ? (S13-03)"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def _review(
    project_id: str,
    *,
    index: int,
    implementer: str,
    reviewer: str,
    caught: bool,
) -> None:
    """Un ticket implémenté par `implementer`, relu par `reviewer`, avec ou sans trouvaille."""
    from choregos_api.db.models import Finding, Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = WorkItem(
            project_id=project_id,
            tracker_key=f"varga/billing-api#{index}",
            title=f"ticket {index}",
            state="verifying",
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                work_item_id=item.id,
                project_id=project_id,
                stage_role="implement",
                attempt=1,
                backend=implementer,
                status="succeeded",
                result={"status": "done"},
            )
        )
        review = Run(
            work_item_id=item.id,
            project_id=project_id,
            stage_role="review",
            attempt=1,
            backend=reviewer,
            status="succeeded",
            result={"status": "done"},
        )
        session.add(review)
        await session.flush()
        if caught:
            session.add(
                Finding(
                    project_id=project_id,
                    origin_run_id=review.id,
                    title=f"trouvaille {index}",
                    type="bug",
                    severity="medium",
                    evidence="repo.py:12",
                )
            )


async def test_sans_assez_de_revues_on_ne_compare_pas(client: AsyncClient, project: dict[str, Any]) -> None:
    await _review(project["id"], index=1, implementer="openhands", reviewer="claude-code", caught=True)
    report = (await client.get(f"/api/v1/projects/{project['id']}/metrics/cross-backend")).json()
    assert report["verdict"] == "échantillon insuffisant"
    assert report["other_backend"]["reviews"] == 1
    assert report["same_backend"]["reviews"] == 0


async def test_la_revue_croisee_attrape_plus(client: AsyncClient, project: dict[str, Any]) -> None:
    for index in range(6):
        await _review(
            project["id"], index=index, implementer="openhands", reviewer="claude-code", caught=True
        )
    for index in range(6, 12):
        await _review(project["id"], index=index, implementer="openhands", reviewer="openhands", caught=False)

    report = (await client.get(f"/api/v1/projects/{project['id']}/metrics/cross-backend")).json()

    assert report["other_backend"] == {
        "reviews": 6,
        "caught": 6,
        "catch_rate": 1.0,
        "backends": ["claude-code"],
    }
    assert report["same_backend"]["catch_rate"] == 0.0
    assert report["verdict"] == "la revue croisée attrape plus"
    assert "100 %" in report["detail"] or "100%" in report["detail"]


async def test_une_revue_sans_implementeur_connu_est_ignoree(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Sans run d'implémentation, on ne sait pas à quoi comparer : on ne compte pas."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"], tracker_key="varga/billing-api#77", title="orphelin", state="verifying"
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="review",
                attempt=1,
                backend="goose",
                status="succeeded",
            )
        )

    report = (await client.get(f"/api/v1/projects/{project['id']}/metrics/cross-backend")).json()
    assert report["same_backend"]["reviews"] == 0
    assert report["other_backend"]["reviews"] == 0


async def test_le_rapport_dit_si_la_politique_l_exige(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project["id"])
        assert row is not None
        model = policy_model(row)
        model.review.cross_backend = True
        row.json_doc = model.model_dump(mode="json", exclude_none=True)

    report = (await client.get(f"/api/v1/projects/{project['id']}/metrics/cross-backend")).json()
    assert report["cross_backend_required"] is True
