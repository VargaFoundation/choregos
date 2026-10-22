"""Netops writes as governed actions, in Temporal (S33-09, ADR 0052): a pfSense rule change runs in
the `ActionWorkflow` once a person approved it, and a later failure puts the previous rule back —
updated, deleted or created, the fake firewall ends as it began. An action nobody approved writes
nothing, even when its workflow is started.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from temporalio.client import WorkflowFailureError

from .conftest import Fixture

CHECK_FAILS = {"effect": "verifier", "with": {"condition": "false", "motif": "the gateway lost its route"}}
CHECK_PASSES = {"effect": "verifier", "with": {"condition": "true", "motif": "the gateway is online"}}


@pytest.fixture
def firewall(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.netops import FakePfSense

    fake = FakePfSense()
    monkeypatch.setattr(choregos_adapters, "FAKE_NETOPS", {"pfsense": fake})
    yield fake


def _with_compensation(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The effect as the broker builds it: the operation, and the compensation its type declares."""
    from choregos_adapters import operation_spec

    declared = operation_spec("firewall", "pfsense", operation)
    assert declared is not None and declared.compensation is not None
    return {
        "effect": "connector.call",
        "with": {"connector": "pfsense", "operation": operation, "arguments": arguments},
        "compensate": {"effect": "connector.call", "with": {"connector": "pfsense", **declared.compensation}},
    }


async def _action(setup: Fixture, effects: list[dict[str, Any]], status: str = "approved") -> str:
    from choregos_adapters import spec_of
    from choregos_api.db.models import Action, ConnectorOperation, OrgConnector, Project
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        connector = (
            await session.execute(select(OrgConnector).where(OrgConnector.name == "pfsense"))
        ).scalar_one_or_none()
        if connector is None:
            connector = OrgConnector(
                org_id=project.org_id,
                name="pfsense",
                kind="firewall",
                type="pfsense",
                config={"base_url": "https://fw.example.test"},
            )
            session.add(connector)
            await session.flush()
            spec = spec_of("firewall", "pfsense")
            assert spec is not None
            for operation in spec.operations:
                session.add(
                    ConnectorOperation(
                        org_id=project.org_id,
                        connector_id=connector.id,
                        name=operation.name,
                        access=operation.access,
                        policy=operation.default_policy,
                        groups=[],
                        input_schema=operation.input_schema,
                    )
                )
        action = Action(
            org_id=project.org_id,
            project_id=project.id,
            origin="tool",
            kind="pfsense.change",
            title="Open 8443 from the LAN",
            params={},
            effects=effects,
            proposed_by={"kind": "agent", "id": "agent:netops"},
            approval={},
            decisions=[{"by": "lea@varga.test", "decision": "approve"}] if status == "approved" else [],
            status=status,
        )
        session.add(action)
        await session.flush()
        return str(action.id)


async def _run(temporal_env: Any, worker_factory: Any, action_id: str) -> dict[str, Any]:
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "ActionWorkflow", {"action_id": action_id}, id=f"action-{action_id}", task_queue="test"
        )
        result: dict[str, Any] = await handle.result()
        return result


async def test_a_failed_check_puts_the_updated_rule_back_as_it_was(
    setup: Fixture, temporal_env: Any, worker_factory: Any, firewall: Any
) -> None:
    before = firewall.state()
    change = _with_compensation(
        "update_firewall_rule", {"tracker": "1700000102", "changes": {"destination_port": "9443"}}
    )
    result = await _run(temporal_env, worker_factory, await _action(setup, [change, CHECK_FAILS]))
    assert result["status"] == "failed" and "lost its route" in result["error"]
    assert [c["undone"] for c in result["compensations"]] == [True]
    assert firewall.state() == before, "the previous rule is restored, field by field, at its place"
    assert firewall.writes == 2, "one change, one restoration"


async def test_a_failed_check_recreates_a_deleted_rule_at_its_position(
    setup: Fixture, temporal_env: Any, worker_factory: Any, firewall: Any
) -> None:
    before = firewall.state()
    change = _with_compensation("delete_firewall_rule", {"tracker": "1700000102"})
    result = await _run(temporal_env, worker_factory, await _action(setup, [change, CHECK_FAILS]))
    assert result["status"] == "failed"

    def without_tracker(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: v for k, v in rule.items() if k != "tracker"} for rule in rules]

    assert without_tracker(firewall.state()) == without_tracker(before), "same rule, same place"
    assert firewall.rules[1]["tracker"] != 1700000102, "pfSense gives a recreated rule a new tracker"


async def test_a_failed_check_deletes_the_rule_the_action_created(
    setup: Fixture, temporal_env: Any, worker_factory: Any, firewall: Any
) -> None:
    before = firewall.state()
    rule = {"type": "pass", "interface": ["lan"], "protocol": "tcp", "source": "lan", "destination": "any",
            "destination_port": "8443", "descr": "LAN to the admin port"}  # fmt: skip
    change = _with_compensation("create_firewall_rule", {"rule": rule})
    result = await _run(temporal_env, worker_factory, await _action(setup, [change, CHECK_FAILS]))
    assert result["status"] == "failed"
    assert firewall.state() == before


async def test_an_approved_change_is_applied_once_and_marked(
    setup: Fixture, temporal_env: Any, worker_factory: Any, firewall: Any
) -> None:
    change = _with_compensation(
        "update_firewall_rule", {"tracker": "1700000102", "changes": {"destination_port": "9443"}}
    )
    result = await _run(temporal_env, worker_factory, await _action(setup, [change, CHECK_PASSES]))
    assert result["status"] == "succeeded"
    rule = firewall.rules[1]
    assert rule["destination_port"] == "9443" and rule["descr"].startswith(
        "WAN to the reverse proxy [choregos:"
    )
    assert firewall.writes == 1


async def test_an_action_nobody_approved_writes_nothing_even_when_its_workflow_starts(
    setup: Fixture, temporal_env: Any, worker_factory: Any, firewall: Any
) -> None:
    before = firewall.state()
    change = _with_compensation(
        "update_firewall_rule", {"tracker": "1700000102", "changes": {"destination_port": "9443"}}
    )
    action_id = await _action(setup, [change], status="pending_approval")
    # `load_action` refuses it before any effect (ADR 0085): the workflow fails, the row stays pending.
    with pytest.raises(WorkflowFailureError) as failure:
        await _run(temporal_env, worker_factory, action_id)
    assert "not approved" in str(failure.value.cause.cause)
    assert firewall.writes == 0 and firewall.state() == before
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = await session.get(Action, action_id)
        assert row is not None and row.status == "pending_approval", "still the approvers' to decide"
