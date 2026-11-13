"""A work item that waits for its action in doubt moves on once a person settles it (S65-26).

A transition's action ends in doubt: its work item waits, as it waits for an approval nobody gave
(S65-14, ADR 0149 §5). A person settles the action through the API's own service: the action is
`approved` again and a new run of `action-<id>` is started after the commit, through the API's own
Temporal client. That run closes the action as any run does and tells the work item: `made` takes the
transition, `not_made` follows its `on_fail`. Nothing under `workflows/` changed for it: the work
item's interpreter and the `ActionWorkflow` are the ones every history replays.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from .conftest import Fixture


@pytest.fixture
def sent() -> Iterator[list[str]]:
    """Every delivery of the write, in order: a settlement must never add one."""
    from choregos_api import effects
    from choregos_api.effects import EffectInDoubt

    deliveries: list[str] = []

    async def doubt(ctx: Any, _params: dict[str, Any]) -> dict[str, Any]:
        deliveries.append(str(ctx.key))
        raise EffectInDoubt("the write was claimed and never answered", sent={"outpost": "factory-floor"})

    effects.declare_effect("test.settle.doubt", doubt, policy="allowed")
    yield deliveries
    effects.reset()


async def _settle(setup: Fixture, action_id: str, outcome: str) -> Any:
    """As a person does through `settleAction`: the API's service, in a committed session."""
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.schemas.actions import ActionSettlementBody
    from choregos_api.services.actions import settle

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        org = await session.get(Organization, project.org_id)
        assert org is not None
        person = SimpleNamespace(
            kind="user",
            user_id=None,
            email="ops@varga.dev",
            authenticated_at=time.time(),
            groups=[],
            org_roles={org.slug: "org_admin"},
            role_for=lambda _org, _project: None,
        )
        body = ActionSettlementBody(outcome=outcome, note="checked in the tracker")  # type: ignore[arg-type]
        action = await settle(session, project, org.slug, action_id, body, person)
        return action.status


async def _waits_then_is_settled(
    setup: Fixture, temporal_env: Any, worker_factory: Any, outcome: str
) -> tuple[dict[str, Any], Any]:
    from choregos_api.temporal import RealTemporal, get_temporal, set_temporal

    from .test_dated_action import _actions, _arrival, _iso, _start, _wait_for, _work_item

    document = _arrival()
    transition = document["transitions"][0]
    transition["action"]["effects"] = [{"effect": "test.settle.doubt", "with": {}}]
    start = await temporal_env.get_current_time()
    await _work_item(setup, document, {"upn": "lea@acme.test", "arrival_date": _iso(start)})
    before = get_temporal()
    address = temporal_env.client.service_client.config.target_host
    set_temporal(RealTemporal(address, temporal_env.client.namespace, "test"))
    try:
        async with worker_factory():
            handle = await _start(temporal_env, setup)
            await _wait_for(handle, lambda s: s.get("action"), "the action is proposed")

            async def status_of_action() -> str:
                (proposed,) = await _actions(setup)
                return str(proposed.status)

            for _ in range(200):
                if await status_of_action() == "in_doubt":
                    break
                await temporal_env.sleep(timedelta(seconds=1))
            assert await status_of_action() == "in_doubt"
            await temporal_env.sleep(timedelta(hours=7))
            assert (await handle.query("status"))["state"] == "prepared", "it waits, as S65-14 left it"

            (action,) = await _actions(setup)
            assert await _settle(setup, action.id, outcome) == "approved"
            for _ in range(200):
                if await status_of_action() in {"succeeded", "failed"}:
                    break
                await temporal_env.sleep(timedelta(seconds=1))
            moved = await _wait_for(handle, lambda s: s["state"] != "prepared", "the work item moves on")
            (action,) = await _actions(setup)
            if moved["state"] != "done":  # a terminal state ends the interpreter by itself
                await handle.signal("control", {"action": "stop"})
            return moved, action
    finally:
        set_temporal(before)


async def test_made_lets_the_work_item_take_its_transition(
    setup: Fixture, temporal_env: Any, worker_factory: Any, sent: list[str]
) -> None:
    moved, action = await _waits_then_is_settled(setup, temporal_env, worker_factory, "made")
    assert action.status == "succeeded"
    assert moved["state"] == "done", moved
    assert sent == [f"{action.id}:0"], "delivered once: the settlement sent nothing"


async def test_not_made_fails_the_action_and_the_work_item_follows_its_on_fail(
    setup: Fixture, temporal_env: Any, worker_factory: Any, sent: list[str]
) -> None:
    moved, action = await _waits_then_is_settled(setup, temporal_env, worker_factory, "not_made")
    assert action.status == "failed"
    # `on_fail` allows one attempt and escalates: the transition is not tried again on its own.
    assert moved["state"] == "to_review", moved
    assert sent == [f"{action.id}:0"], "delivered once: neither the settlement nor `on_fail` sent it again"
