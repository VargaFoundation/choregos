"""Is a guarantee shipped OUTSIDE this tree accepted by the validator and evaluated?

The extension seam (`choregos.plugins`) lets a package installed alongside register its
connectors. The plan assumed that a *guarantee* would need four changes in the core:
the decorated function, a field on `GateContext`, its population in the orchestrator, and
`GATES_NEEDING_PARAMS` for the static refusal.

This test puts the assumption to the proof. What matters is not whether it is right or wrong,
but knowing: as long as it has not been tried, "it is extensible" is an opinion.

The real question is the first one: the DSL refuses an unknown guarantee with a blocking ERROR
(`gate.unknown`). A guarantee registered after startup must therefore become known to the
validator, otherwise a workflow that uses it is invalid and nothing ever runs.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import Evidence, StageResult
from choregos_core import parse_workflow
from choregos_core.gates import GateContext, GateOutcome, evaluate, gate, known_gates

NAME = "clean_collector"

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: house, version: 1 }
actors:
  house_agent: { type: agent, role: implement, model: "profile:standard" }
  ci: { type: system }
states:
  inbox: { display: To sort, kind: wait }
  verification: { display: Verification }
  finished: { display: Finished, terminal: true }
transitions:
  - id: t-do
    from: inbox
    to: verification
    by: house_agent
    outputs: [report]
    on_fail: { to: inbox, max_attempts: 2, escalate_to: finished }
  - id: t-verify
    from: verification
    to: finished
    by: ci
    gates: [clean_collector]
"""


@pytest.fixture
def house_guarantee() -> Any:
    """Register a guarantee as an extension would, then remove it."""
    from choregos_core.gates import _REGISTRY

    before = dict(_REGISTRY)

    @gate(NAME)
    def _clean_collector(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
        """Read what the stage produced; nothing else is needed."""
        facts = (ctx.result.evidence.facts or {}) if ctx.result else {}
        clean = bool(facts.get("clean_collector"))
        return GateOutcome(NAME, clean, detail="collector clean" if clean else "finding still there")

    yield
    _REGISTRY.clear()
    _REGISTRY.update(before)


def test_without_the_extension_the_workflow_is_invalid() -> None:
    """The proof that the test does not lie to itself: the guarantee must really be missing at the start."""
    assert NAME not in known_gates()
    _, report = parse_workflow(WORKFLOW, strict=False)
    codes = [error.code for error in report.errors]
    assert "gate.unknown" in codes, f"expected an unknown-guarantee refusal, got {codes}"


def test_with_the_extension_the_validator_accepts_it(house_guarantee: None) -> None:
    """This is the property that decides everything: without it, the workflow never starts."""
    assert NAME in known_gates()
    _, report = parse_workflow(WORKFLOW, strict=False)
    assert report.valid, [f"{e.code}: {e.message}" for e in report.errors]


def test_and_it_is_evaluated_on_the_stage_result(house_guarantee: None) -> None:
    """No field added to `GateContext`: the guarantee reads `result`, which already carries the facts."""
    for value, expected in ((True, True), (False, False)):
        result = StageResult(
            schema="choregos/StageResult/v1",
            status="done",
            summary="trial",
            evidence=Evidence(facts={"clean_collector": value}),
        )
        verdict = evaluate(NAME, GateContext(result=result))
        assert verdict.passed is expected
        assert verdict.blocking is (not expected), "a guarantee that does not pass must block"
