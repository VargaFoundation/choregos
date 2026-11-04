"""Terraform: the `apply` leaves with the train, after approval, and never without Atlantis (S9-05)."""

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

    item: dict[str, Any] = {"work_item_key": setup.tracker_key, "title": "a ticket"}
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
        assert model.release_train.get("prod") is not None, "the test preset has a prod train"
        doc = model.model_dump(mode="json", exclude_none=True)
        doc["release_train"]["prod"]["terraform"] = {"via": "atlantis", "apply_requires_approval": True}
        row.json_doc = doc


def _seed_pr(setup: Fixture) -> None:
    from choregos_core.domain import PrRef, PrState

    ref = PrRef(repo="varga/billing-api-infra", number=7, url=INFRA_PR)
    setup.adapters.scm.prs[("varga/billing-api-infra", 7)] = PrState(ref=ref, title="infra")


async def test_the_apply_is_triggered_on_the_infra_pr(setup: Fixture) -> None:
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


async def test_a_failed_apply_stops_the_departure(setup: Fixture) -> None:
    await _enable_atlantis(setup.project_id)
    _seed_pr(setup)
    setup.adapters.scm.atlantis("failure")
    release_id = await _release(setup, with_infra=True)

    outcome = await train_activities.apply_terraform(
        {"project_slug": setup.project_slug, "env": "prod", "release_id": release_id}
    )

    assert outcome["ok"] is False
    assert "failure" in outcome["reason"]
    titles = [message.title for _, message in setup.adapters.notify.sent]
    assert any("Terraform" in title for title in titles), titles


async def test_without_an_infra_pr_the_train_comments_nothing(setup: Fixture) -> None:
    await _enable_atlantis(setup.project_id)
    release_id = await _release(setup, with_infra=False)

    outcome = await train_activities.apply_terraform(
        {"project_slug": setup.project_slug, "env": "prod", "release_id": release_id}
    )

    assert outcome["applied"] == []
    assert "no infrastructure pull request" in outcome["skipped"]
    assert setup.adapters.scm.comments == []


async def test_without_atlantis_configured_nothing_happens(setup: Fixture) -> None:
    """`terraform.via: none`: commenting on a PR would be a surprise."""
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

    assert "Atlantis is not configured" in outcome["skipped"]
    assert setup.adapters.scm.comments == []


async def test_an_unreadable_pr_url_is_refused(setup: Fixture) -> None:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope

    await _enable_atlantis(setup.project_id)
    release_id = await _release(setup, with_infra=True)
    async with session_scope() as session:
        release = await session.get(Release, release_id)
        assert release is not None
        release.items = [{"work_item_key": "x", "infra_pr_url": "not-a-url"}]

    outcome = await train_activities.apply_terraform(
        {"project_slug": setup.project_slug, "env": "prod", "release_id": release_id}
    )

    assert outcome["ok"] is False
    assert "unreadable" in outcome["reason"]


async def test_the_infra_pr_declared_by_the_agent_follows_the_ticket(setup: Fixture) -> None:
    """`artifacts.reports["infra_pr"]` is the channel: no new contract for this."""
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

    result = stage_result("infra and code", artifacts={"reports": {"infra_pr": INFRA_PR}})
    await stage_activities.record_run_outcome(
        {"run_id": "run-infra", "result": result.model_dump(mode="json", by_alias=True)}
    )

    from choregos_api.db.models import WorkItem

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        assert item.documents["infra_pr_url"] == INFRA_PR
