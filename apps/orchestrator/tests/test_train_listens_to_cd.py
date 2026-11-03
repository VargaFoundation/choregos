"""The train listens to Argo CD during the departure (#280, S22-18).

The `/webhooks/argocd` webhook signals `deploy_event` to the environment's train. Until now the train
filed the event and never read it: an application announced as degraded during a 20-minute soak
waited for the step's next poll. Here, the step is held open (its observation does not hand control
back): only the event can make the departure roll back.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import Fixture, recording_history

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


class HeldSteps:
    """The observation of the named steps hands control back only on `release()`: simulated it is
    instantaneous, and the poll would conclude before the test could send anything."""

    def __init__(self, *steps: str) -> None:
        self.steps = set(steps)
        self.running: set[str] = set()
        self._free = asyncio.Event()

    async def observe(self, seconds: float) -> None:
        from temporalio import activity

        step = activity.info().activity_type
        if step not in self.steps:
            return
        self.running.add(step)
        try:
            await self._free.wait()
        finally:
            self.running.discard(step)

    def release(self) -> None:
        self._free.set()


@pytest.fixture
def holds(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from choregos_orchestrator.activities import train as train_activities

    created: list[HeldSteps] = []

    def hold_steps(*steps: str) -> HeldSteps:
        hold = HeldSteps(*steps)
        created.append(hold)
        monkeypatch.setattr(train_activities, "_observe", hold.observe)
        return hold

    yield hold_steps
    for hold in created:
        hold.release()


def _argocd(type_: str, *, env: str = "prod", app: str = "billing-api", n: int = 1) -> dict[str, Any]:
    """What `/webhooks/argocd` signals to the train (a serialised `InboundEvent`)."""
    return {
        "type": type_,
        "source": "argocd",
        "delivery_id": f"argo-{n}",
        "project_slug": "billing-api",
        "payload": {"app": app, "revision": "abc123", "env": env},
    }


async def _train(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "ReleaseTrain",
        {"project_slug": setup.project_slug, "env": "prod"},
        id=f"train-{setup.project_slug}-prod",
        task_queue="test",
    )


async def _until(predicate: Any, timeout: float = 30.0) -> None:
    for _ in range(int(timeout * 10)):
        if await predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition never reached")


async def _state(handle: Any) -> dict[str, Any]:
    return dict(await handle.query("status_query"))


async def _releases() -> list[Any]:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(Release))).scalars())


async def test_a_degradation_announced_during_the_soak_rolls_back_without_waiting(
    setup: Fixture, temporal_env: Any, worker_factory: Any, holds: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    soak = holds("soak")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#7", "sha": "f1"})
            await train.signal("depart_now", {"by": "augustin"})
            await _until(lambda: _truth("soak" in soak.running))
            assert (await _state(train))["status"] == "staging"

            await train.signal("deploy_event", _argocd("cd.app.degraded"))
            # The soak is still held: only the event can have decided the rollback.
            await _until(lambda: _is_frozen(train), timeout=20)
            assert "soak" in soak.running, "the soak did not conclude: the event decided"
            state = await _state(train)
            assert state["freeze_reason"] == "Argo CD reported billing-api degraded"

            await train.signal("abort", {"by": "test"})
            soak.release()
            await train.result()
            history = await train.fetch_history()

    (release,) = await _releases()
    assert release.status == "rolled_back"
    assert release.verdict == {"go": False, "reason": "Argo CD reported billing-api degraded"}
    assert "billing-api" in setup.adapters.cd.aborted, "the rollback cancelled the rollout"
    titles = [message.title for _, message in setup.adapters.notify.sent]
    assert not any(t.startswith("Approval requested") for t in titles), "the departure stopped at the soak"

    # The history replays against the current code, and is archived for those to come.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "train-listens-to-cd-S22-18.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_rollout_aborted_during_the_canary_rolls_back(
    setup: Fixture, temporal_env: Any, worker_factory: Any, holds: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    canary = holds("promote_canary_step")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#8", "sha": "g1"})
            await train.signal("depart_now", {"by": "augustin"})
            await _until(lambda: _has_status(train, "awaiting_approval"))
            await train.signal("approve", {"by": "marie"})
            await _until(lambda: _truth("promote_canary_step" in canary.running))

            await train.signal("deploy_event", _argocd("cd.rollout.aborted"))
            await _until(lambda: _is_frozen(train), timeout=20)
            state = await _state(train)
            assert state["freeze_reason"] == "Argo CD reported the billing-api rollout aborted"

            await train.signal("abort", {"by": "test"})
            canary.release()
            await train.result()

    (release,) = await _releases()
    assert release.status == "rolled_back"


async def test_the_train_only_listens_to_its_environment_and_its_applications(
    setup: Fixture, temporal_env: Any, worker_factory: Any, holds: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    soak = holds("soak")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#9", "sha": "h1"})
            await train.signal("depart_now", {"by": "augustin"})
            await _until(lambda: _truth("soak" in soak.running))

            for n, event in enumerate(
                (
                    _argocd("cd.app.degraded", env="staging"),
                    _argocd("cd.app.degraded", app="other-app"),
                    _argocd("cd.app.synced"),
                    _argocd("cd.rollout.completed"),
                ),
                start=1,
            ):
                await train.signal("deploy_event", {**event, "delivery_id": f"argo-{n}"})
            await asyncio.sleep(1.5)
            state = await _state(train)
            assert state["status"] == "staging" and state["frozen"] is False, state

            # The poll, for its part, concludes normally: the departure goes as far as the approval.
            soak.release()
            await _until(lambda: _has_status(train, "awaiting_approval"))
            assert (await _state(train))["frozen"] is False
            await train.signal("abort", {"by": "test"})
            await train.result()

    (release,) = await _releases()
    assert release.status != "rolled_back"
    assert not setup.adapters.cd.aborted


async def test_an_alert_received_before_the_departure_does_not_roll_it_back(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """What Argo CD announces during the collection is about the previous release: the next departure
    does not read it again (the smoke test looks at the real state right after the promotion)."""
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await _until(lambda: _has_status(train, "collecting"))
            await train.signal("deploy_event", _argocd("cd.app.degraded"))
            await train.signal("merged", {"work_item_key": "varga/billing-api#10", "sha": "i1"})
            await train.signal("depart_now", {"by": "augustin"})
            await _until(lambda: _has_status(train, "awaiting_approval"))
            await train.signal("approve", {"by": "marie"})
            await _until(lambda: _has_status(train, "collecting"), timeout=60)
            assert (await _state(train))["frozen"] is False
            await train.signal("abort", {"by": "test"})
            await train.result()

    (release,) = await _releases()
    assert release.status == "done"


async def _truth(value: bool) -> bool:
    return value


async def _has_status(handle: Any, status: str) -> bool:
    return (await _state(handle))["status"] == status


async def _is_frozen(handle: Any) -> bool:
    return bool((await _state(handle))["frozen"])
