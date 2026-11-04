"""The broker lives in `services/broker.py` in English (S46-18); its former `services/courtier.py` is gone.

What these tests hold: the id of a governed write is persisted, so it must not change with the move,
and a broker tool announces itself in English.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from choregos_api.services import broker

#: Computed from `identifiant_de_l_appel` as it was before the move (uuid5 of `run:tool:canonical JSON`).
CALL_ID = "c6a66a5a-d541-5b95-a6ab-1cc8ed2a3492"
GOVERNED_WRITE = "runs as a governed action: journaled, done once, undone if a later step fails"
NEEDS_APPROVAL = "needs a human approval: calling it proposes the action, it does not run it"


def test_call_ids_are_unchanged() -> None:
    """The id is persisted as `actions.id`: a replayed call must find the action it already made."""
    arguments = {"model": "x14", "n": 1}

    assert broker.call_id("run-write-1", "supplier__order_pc", arguments) == CALL_ID
    # The arguments are canonical: their order does not change the call.
    assert broker.call_id("run-write-1", "supplier__order_pc", {"n": 1, "model": "x14"}) == CALL_ID


def _tool(*, needs_approval: bool) -> broker.BrokerTool:
    connector = SimpleNamespace(name="supplier", kind="mcp", type="mcp")
    operation = SimpleNamespace(
        name="order_pc",
        access="write",
        description="Order a PC",
        input_schema={},
        output_schema=None,
    )
    return broker.BrokerTool("supplier__order_pc", cast(Any, connector), cast(Any, operation), needs_approval)


def test_a_broker_tool_reads_and_announces_itself_in_english() -> None:
    tool = _tool(needs_approval=False)

    assert tool.name == "supplier__order_pc"
    assert tool.is_write is True
    assert tool.needs_approval is False
    announced = tool.announcement()
    assert announced["name"] == "supplier__order_pc"
    assert announced["inputSchema"] == {"type": "object"}
    assert announced["description"] == f"[supplier] Order a PC — {GOVERNED_WRITE}"


def test_a_tool_under_approval_announces_the_approval_sentence() -> None:
    tool = _tool(needs_approval=True)

    assert tool.needs_approval is True
    assert tool.announcement()["description"] == f"[supplier] Order a PC — {NEEDS_APPROVAL}"
