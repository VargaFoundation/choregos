"""The gate seam of ADR 0181 (C0): an extension's gate reads the facts of its context provider, and a gate
this deployment cannot judge is refused when the workflow is published, naming the plugin.

`test_gate_outside_the_tree.py` proved that a gate registered by an extension is known to the validator
and judged on the stage result. A trade's gate needs more: something read outside the result (a ticket's
state, a pipeline's checks), which the core does not know how to read. The extension declares a
**context provider** for it, and the gate activity files what the provider returns in
`GateContext.facts`, under the provider's name.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from tranibor_core import parse_workflow
from tranibor_core.errors import GateError
from tranibor_core.gates import (
    PLUGIN_GATES,
    GateContext,
    GateOutcome,
    ProviderRequest,
    context_provider,
    evaluate,
    gate,
    gate_plugin,
    known_providers,
    provide_facts,
    unavailable,
)

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: tickets, version: 1 }
actors:
  agent: { type: agent, role: triage, model: "profile:standard" }
  desk: { type: system }
states:
  inbox: { display: To sort, kind: wait }
  resolving: { display: Resolving }
  closed: { display: Closed, terminal: true }
transitions:
  - id: t-sort
    from: inbox
    to: resolving
    by: agent
    outputs: [report]
    on_fail: { to: inbox, max_attempts: 2, escalate_to: closed }
  - id: t-close
    from: resolving
    to: closed
    by: desk
    gates: [GATE]
"""


@pytest.fixture
def registries() -> Iterator[None]:
    """Whatever a test registers is removed after it, as if the extension had never been installed."""
    from tranibor_core import gates

    tables = ("_REGISTRY", "_NEEDS", "_READS", "_PLUGIN_OF", "_PROVIDERS", "_PROVIDER_PLUGIN")
    saved = {name: dict(getattr(gates, name)) for name in tables}
    asynchronous = set(gates._ASYNC_GATES)
    yield
    for name, content in saved.items():
        table = getattr(gates, name)
        table.clear()
        table.update(content)
    gates._ASYNC_GATES.clear()
    gates._ASYNC_GATES.update(asynchronous)


def _register_ticket_gate(*, provider: bool = True) -> list[ProviderRequest]:
    """What the `itsm` plugin's extension would do when it loads: a gate, and the provider it reads."""
    seen: list[ProviderRequest] = []

    @gate("ticket_resolved", reads=("itsm.tickets",), plugin="itsm")
    def _ticket_resolved(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
        tickets = ctx.facts.get("itsm.tickets")
        if tickets is None:
            return GateOutcome("ticket_resolved", False, detail="the ticket's state could not be read")
        state = tickets.get("state")
        wanted = params.get("state", "resolved")
        return GateOutcome("ticket_resolved", state == wanted, detail=f"ticket {state}")

    if provider:

        @context_provider("itsm.tickets", plugin="itsm")
        async def _tickets(request: ProviderRequest) -> Mapping[str, Any]:
            seen.append(request)
            return {"state": request.fields.get("ticket_state")}

    return seen


def _publish(gate_name: str) -> list[tuple[str, str]]:
    _, report = parse_workflow(WORKFLOW.replace("GATE", gate_name), strict=False)
    return [(error.code, error.message) for error in report.errors]


def test_the_provider_s_facts_are_filed_under_its_name_and_the_gate_judges_them(registries: None) -> None:
    seen = _register_ticket_gate()
    declarations = [{"name": "ticket_resolved", "params": {"state": "closed"}}, {"name": "outputs_present"}]
    request = ProviderRequest(project_id="p", work_item_id="w", fields={"ticket_state": "closed"})
    facts = asyncio.run(provide_facts(declarations, request))
    assert facts == {"itsm.tickets": {"state": "closed"}}
    (asked,) = seen
    assert asked.gates == (("ticket_resolved", {"state": "closed"}),), "told which gates read it"
    assert evaluate("ticket_resolved", GateContext(facts=facts), {"state": "closed"}).passed
    assert not evaluate("ticket_resolved", GateContext(facts=facts), {"state": "resolved"}).passed


def test_a_provider_no_declared_gate_reads_is_not_called(registries: None) -> None:
    seen = _register_ticket_gate()
    facts = asyncio.run(provide_facts([{"name": "outputs_present"}], ProviderRequest("p", "w")))
    assert facts == {} and seen == []


def test_a_failing_provider_leaves_no_facts_and_its_gate_refuses(registries: None) -> None:
    _register_ticket_gate(provider=False)

    @context_provider("itsm.tickets", plugin="itsm")
    async def _down(request: ProviderRequest) -> Mapping[str, Any]:
        raise ConnectionError("the ITSM is unreachable")

    facts = asyncio.run(provide_facts([{"name": "ticket_resolved"}], ProviderRequest("p", "w")))
    assert facts == {}
    verdict = evaluate("ticket_resolved", GateContext(facts=facts))
    assert verdict.blocking and "could not be read" in verdict.detail


def test_two_providers_under_one_name_are_refused(registries: None) -> None:
    _register_ticket_gate()

    async def _other(request: ProviderRequest) -> Mapping[str, Any]:
        return {}

    with pytest.raises(GateError, match="already declared by the plugin `itsm`"):
        context_provider("itsm.tickets", plugin="other")(_other)


def test_with_its_extension_the_workflow_is_published(registries: None) -> None:
    _register_ticket_gate()
    assert "itsm.tickets" in known_providers()
    assert gate_plugin("ticket_resolved") == "itsm"
    assert _publish("ticket_resolved") == []


def test_a_gate_whose_provider_is_not_installed_is_refused_at_publication_naming_its_plugin(
    registries: None,
) -> None:
    _register_ticket_gate(provider=False)
    [(code, message)] = _publish("ticket_resolved")
    assert code == "gate.provider_missing"
    assert "`ticket_resolved` of the plugin `itsm`" in message and "`itsm.tickets`" in message


def test_a_gate_nobody_provides_is_refused_at_publication() -> None:
    [(code, message)] = _publish("ticket_resolved")
    assert code == "gate.unknown"
    assert "no installed extension provides it" in message


def test_a_plugin_s_gate_without_its_extension_is_refused_naming_the_plugin(registries: None) -> None:
    """What a stored software-delivery workflow meets once the twelve gates have moved (C2) on a
    deployment without the plugin's extension: here simulated by unregistering `ci_green`."""
    from tranibor_core import gates

    assert PLUGIN_GATES["ci_green"] == "software-delivery"
    assert unavailable("ci_green") is None, "in C0 the core still serves it"
    del gates._REGISTRY["ci_green"]
    [(code, message)] = _publish("ci_green")
    assert code == "gate.unknown"
    assert "served by the plugin `software-delivery`, whose extension is not installed" in message
    with pytest.raises(GateError, match="plugin `software-delivery`"):
        evaluate("ci_green", GateContext())


def test_the_twelve_plugin_gates_are_the_ones_that_need_the_scm() -> None:
    from tranibor_core.gates import _NEEDS

    assert set(PLUGIN_GATES) == {name for name, needs in _NEEDS.items() if "scm" in needs}
    assert len(PLUGIN_GATES) == 12
