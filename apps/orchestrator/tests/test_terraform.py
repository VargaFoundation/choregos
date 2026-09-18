"""Terraform : l'`apply` part du train, après approbation, et jamais sans Atlantis (S9-05)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_orchestrator.activities import train as train_activities

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

INFRA_PR = "https://github.com/varga/billing-api-infra/pull/7"


async def _release(setup: Fixture, *, with_infra: bool) -> str:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    item: dict[str, Any] = {"work_item_key": setup.tracker_key, "title": "un ticket"}
    if with_infra:
        item["infra_pr_url"] = INFRA_PR
    async with session_scope() as session:
        release = Release(
            project_id=setup.project_id,
            env="prod",
            batch_no=1,
            status="promoting",
            items=[item],
            started_at=utcnow(),
        )
        session.add(release)
        await session.flush()
        return release.id


async def _enable_atlantis(project_id: str) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row)
        assert model.release_train.get("prod") is not None, "le preset de test a un train prod"
        doc = model.model_dump(mode="json", exclude_none=True)
        doc["release_train"]["prod"]["terraform"] = {"via": "atlantis", "apply_requires_approval": True}
        row.json_doc = doc


def _seed_pr(setup: Fixture) -> None:
    from choregos_core.domain import PrRef, PrState

    ref = PrRef(repo="varga/billing-api-infra", number=7, url=INFRA_PR)
    setup.adapters.scm.prs[("varga/billing-api-infra", 7)] = PrState(ref=ref, title="infra")


async def test_l_apply_est_declenche_sur_la_pr_d_infra(setup: Fixture) -> None:
    await _enable_atlantis(setup.project_id)
    _seed_pr(setup)
    setup.adapters.scm.atlantis("success")
    release_id = await _release(setup, with_infra=True)

    outcome = await train_activities.apply_terraform(
        {"project_slug": setup.project_slug, "env": "prod", "release_id": release_id}
    )

    assert outcome["ok"] is True
    assert outcome["applied"] == [INFRA_PR]
    assert ("varga/billing-api-infra", 7, "atlantis apply") in setup.adapters.scm.comments


async def test_un_apply_en_echec_arrete_le_depart(setup: Fixture) -> None:
    await _enable_atlantis(setup.project_id)
    _seed_pr(setup)
    setup.adapters.scm.atlantis("failure")
    release_id = await _release(setup, with_infra=True)

    outcome = await train_activities.apply_terraform(
        {"project_slug": setup.project_slug, "env": "prod", "release_id": release_id}
    )

    assert outcome["ok"] is False
    assert "failure" in outcome["reason"]
    titres = [message.title for _, message in setup.adapters.notify.sent]
    assert any("Terraform" in titre for titre in titres), titres


async def test_sans_pr_d_infra_le_train_ne_commente_rien(setup: Fixture) -> None:
    await _enable_atlantis(setup.project_id)
    release_id = await _release(setup, with_infra=False)

    outcome = await train_activities.apply_terraform(
        {"project_slug": setup.project_slug, "env": "prod", "release_id": release_id}
    )

    assert outcome["applied"] == []
    assert "aucune PR d'infra" in outcome["skipped"]
    assert setup.adapters.scm.comments == []


async def test_sans_atlantis_configure_rien_ne_se_passe(setup: Fixture) -> None:
    """`terraform.via: none` : commenter une PR serait une surprise."""
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        doc = policy_model(row).model_dump(mode="json", exclude_none=True)
        doc["release_train"]["prod"]["terraform"] = {"via": "none"}
        row.json_doc = doc

    _seed_pr(setup)
    release_id = await _release(setup, with_infra=True)

    outcome = await train_activities.apply_terraform(
        {"project_slug": setup.project_slug, "env": "prod", "release_id": release_id}
    )

    assert "atlantis non configuré" in outcome["skipped"]
    assert setup.adapters.scm.comments == []


async def test_une_url_de_pr_illisible_est_refusee(setup: Fixture) -> None:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope

    await _enable_atlantis(setup.project_id)
    release_id = await _release(setup, with_infra=True)
    async with session_scope() as session:
        release = await session.get(Release, release_id)
        assert release is not None
        release.items = [{"work_item_key": "x", "infra_pr_url": "pas-une-url"}]

    outcome = await train_activities.apply_terraform(
        {"project_slug": setup.project_slug, "env": "prod", "release_id": release_id}
    )

    assert outcome["ok"] is False
    assert "illisible" in outcome["reason"]


async def test_la_pr_d_infra_declaree_par_l_agent_suit_le_ticket(setup: Fixture) -> None:
    """`artifacts.reports["infra_pr"]` est le canal : pas de contrat neuf pour ça."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as stage_activities

    from .conftest import stage_result

    async with session_scope() as session:
        run = Run(
            id="run-infra",
            work_item_id=setup.work_item_id,
            project_id=setup.project_id,
            stage_role="implement",
            attempt=1,
            status="running",
        )
        session.add(run)

    result = stage_result("infra et code", artifacts={"reports": {"infra_pr": INFRA_PR}})
    await stage_activities.record_run_outcome(
        {"run_id": "run-infra", "result": result.model_dump(mode="json", by_alias=True)}
    )

    from choregos_api.db.models import WorkItem

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        assert item.documents["infra_pr_url"] == INFRA_PR
