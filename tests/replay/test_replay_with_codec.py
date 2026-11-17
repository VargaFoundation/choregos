"""A history recorded with the payload codec carries no ticket text, and replays with the codec (ADR 0183).

The archived histories of `histories/` were recorded in clear and replay with the default converter:
the codec is off by default, so they stay what they are. This file records a fresh run WITH the
codec, exports its history as an operator would (`fetch_history().to_json()`), and checks that:

- the ticket's text is nowhere in the export, nor in the raw history bytes, where the same run in
  clear has it (the control that keeps the check honest);
- the history replays against the current code with the deployment's converter;
- it does not replay without the key: the codec really is in the path.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import pytest
from choregos_core.temporal_connection import TemporalSecurity, data_converter, parse_codec_keys

TICKET = "HR-77 Salary review for Jane Doe: 81500 EUR, medical leave since March"


def _stub_activities() -> list[Any]:
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        # An activity's result lands in the history as much as its input does.
        return {"mode": "auto_sync", "summary": TICKET}

    return [load_train_config]


async def _record(converter: Any) -> Any:
    """Run one ReleaseTrain under `converter`; the ticket is in its input, an activity's result and a
    signal. Returns its history."""
    from choregos_orchestrator.testing import workers_by_queue
    from choregos_orchestrator.workflows.train import ReleaseTrain
    from temporalio.testing import WorkflowEnvironment

    environment = await WorkflowEnvironment.start_time_skipping(data_converter=converter)
    try:
        async with workers_by_queue(
            environment.client,
            activities=_stub_activities(),
            workflows=[ReleaseTrain],
            workflow_queue="replay",
        ):
            handle = await environment.client.start_workflow(
                "ReleaseTrain",
                {"project_slug": "codec", "env": "dev", "carried_items": [{"key": "HR-77", "title": TICKET}]},
                id="train-codec-dev",
                task_queue="replay",
            )
            await handle.signal("abort", {"by": TICKET})
            await handle.result()
            return await handle.fetch_history()
    finally:
        await environment.shutdown()


def _security() -> TemporalSecurity:
    return TemporalSecurity(codec_keys=parse_codec_keys(f"k1:{base64.b64encode(os.urandom(32)).decode()}"))


def _leaks(history: Any) -> bool:
    exported = history.to_json()
    raw = b"".join(event.SerializeToString() for event in history.events)
    encoded = base64.b64encode(TICKET.encode()).decode()
    return TICKET in exported or TICKET.encode() in raw or encoded in exported


async def test_a_run_with_the_codec_leaves_no_ticket_text_in_the_exported_history() -> None:
    from temporalio.converter import DataConverter

    assert _leaks(await _record(DataConverter.default)), "the control: in clear, the ticket is in the history"
    assert not _leaks(await _record(data_converter(_security())))


async def test_a_history_recorded_with_the_codec_replays_with_the_codec_and_not_without() -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.converter import DataConverter
    from temporalio.worker import Replayer

    converter = data_converter(_security())
    archived = json.loads((await _record(converter)).to_json())
    history = WorkflowHistory.from_json("train-codec-dev", archived)

    await Replayer(workflows=ALL_WORKFLOWS, data_converter=converter).replay_workflow(history)

    with pytest.raises(RuntimeError, match="Failed decoding arguments"):
        await Replayer(workflows=ALL_WORKFLOWS, data_converter=DataConverter.default).replay_workflow(history)
