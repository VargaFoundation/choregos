# SPDX-License-Identifier: Apache-2.0
"""An INSTALLED extension's gate is judged by the gate activity on the facts of its context provider
(ADR 0181, C0).

The extension is a real module with its `.dist-info`: `load_extensions()` finds it and calls its
`plug_in()`, as at a worker's start-up. It is a test plugin, `itsm`, with one gate, `ticket_resolved`,
that reads the facts of its provider `itsm.tickets`. Nothing of it is in the core: the activity calls the
provider because the gate declares it reads it, and files what it returns in `GateContext.facts`.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

EXTENSION = """
from tranibor_core.gates import GateOutcome, context_provider, gate

#: What the test sets: the tickets' states, the requests the provider saw, an outage.
STATE = {"tickets": {}, "seen": [], "outage": False}


async def tickets(request):
    STATE["seen"].append(request)
    if STATE["outage"]:
        raise ConnectionError("the ITSM is unreachable")
    return {"state": STATE["tickets"].get(request.fields.get("ticket"))}


def ticket_resolved(ctx, params):
    found = ctx.facts.get("itsm.tickets")
    if found is None:
        return GateOutcome("ticket_resolved", False, detail="the ticket's state could not be read")
    state = found.get("state")
    return GateOutcome("ticket_resolved", state == params.get("state", "resolved"), detail=f"ticket {state}")


def plug_in():
    context_provider("itsm.tickets", plugin="itsm")(tickets)
    gate("ticket_resolved", reads=("itsm.tickets",), plugin="itsm")(ticket_resolved)
"""


@pytest.fixture
def extension(tmp_path: pathlib.Path) -> Iterator[dict[str, Any]]:
    from tranibor_adapters import load_extensions
    from tranibor_core import gates

    tables = ("_REGISTRY", "_NEEDS", "_READS", "_PLUGIN_OF", "_PROVIDERS", "_PROVIDER_PLUGIN")
    saved = {name: dict(getattr(gates, name)) for name in tables}
    root = tmp_path / "site"
    root.mkdir()
    (root / "extension_itsm.py").write_text(EXTENSION, encoding="utf-8")
    info = root / "extension_itsm-0.1.0.dist-info"
    info.mkdir()
    metadata = "Metadata-Version: 2.1\nName: extension_itsm\nVersion: 0.1.0\n"
    (info / "METADATA").write_text(metadata, encoding="utf-8")
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nextension_itsm = extension_itsm:plug_in\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    try:
        assert "extension_itsm" in load_extensions()
        yield sys.modules["extension_itsm"].STATE
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("extension_itsm", None)
        for name, content in saved.items():
            getattr(gates, name).clear()
            getattr(gates, name).update(content)


async def _with_ticket(setup: Fixture, ticket: str) -> None:
    from tranibor_api.db.models import WorkItem
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.fields = {**(item.fields or {}), "ticket": ticket}


def _charge(setup: Fixture, **params: Any) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "gates": [{"name": "ticket_resolved", "params": params}, {"name": "external", "params": {}}],
    }


async def test_the_gate_passes_and_fails_on_its_provider_s_facts(
    setup: Fixture, extension: dict[str, Any]
) -> None:
    from tranibor_orchestrator.activities.gates import evaluate_gates

    await _with_ticket(setup, "INC-42")
    extension["tickets"]["INC-42"] = "open"
    verdict, _ = await evaluate_gates(_charge(setup))
    assert (verdict["name"], verdict["passed"]) == ("ticket_resolved", False)
    assert verdict["detail"] == "ticket open"

    extension["tickets"]["INC-42"] = "resolved"
    verdict, _ = await evaluate_gates(_charge(setup))
    assert verdict["passed"] is True, verdict

    # Asked once per evaluation, with what it needs and only the gates that read it.
    request = extension["seen"][-1]
    assert (request.project_id, request.work_item_id) == (setup.project_id, setup.work_item_id)
    assert request.gates == (("ticket_resolved", {}),)
    assert request.fields["ticket"] == "INC-42" and request.project is not None
    assert len(extension["seen"]) == 2


async def test_a_provider_that_fails_makes_its_gate_refuse_not_the_evaluation(
    setup: Fixture, extension: dict[str, Any]
) -> None:
    from tranibor_orchestrator.activities.gates import evaluate_gates

    await _with_ticket(setup, "INC-42")
    extension["tickets"]["INC-42"] = "resolved"
    extension["outage"] = True
    verdict, other = await evaluate_gates(_charge(setup))
    assert verdict["passed"] is False and "could not be read" in verdict["detail"]
    assert other["name"] == "external", "the other gates are judged as usual"


async def test_no_provider_is_called_for_gates_that_read_none(
    setup: Fixture, extension: dict[str, Any]
) -> None:
    from tranibor_orchestrator.activities.gates import evaluate_gates

    await evaluate_gates(
        {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "gates": [{"name": "external"}]}
    )
    assert extension["seen"] == []
