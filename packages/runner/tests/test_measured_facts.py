"""Is a business fact MEASURED, or only told?

`evidence_facts` (ADR 0012) opened the evidence to the trade: `profiles_kept: 3`,
`identity_document: true`. But these facts were written by the agent, so believable and not
verifiable, while ADR 0010 demands that a guarantee be a mechanism. The tests, for their part, have
always been measured: the runner runs them and overwrites what the agent declares.

`dod.facts` extends this treatment to named facts: one command per fact, exit code 0 = true.

Two properties are worth the detour:

  * the commands live at the ROOT of the configuration, not under `repo`: a project without a
    repository (a case file to examine, a platform to maintain) must be able to prove something;
  * the merge is done **by key**. The agent may name five facts of which only one is measured;
    overwriting the whole dictionary would lose the other four, silently.
"""

from __future__ import annotations

import pathlib

import pytest
from choregos_contracts import Evidence, StageInput
from choregos_runner.dod import merge_evidence, run_checks

pytestmark = pytest.mark.asyncio


@pytest.fixture
def make_input(stage_input: StageInput):
    """The shared fixture, of which we change ONLY the fact commands, and we switch off the
    other three: this test is about facts, not about the repository's tests."""

    def _with(facts: dict[str, str]) -> StageInput:
        project = stage_input.project.model_copy(
            update={
                "fact_commands": facts,
                "test_command": "",
                "lint_command": "",
                "typecheck_command": "",
            }
        )
        return stage_input.model_copy(update={"project": project})

    return _with


async def test_a_command_that_succeeds_yields_a_true_fact(make_input, tmp_path: pathlib.Path) -> None:
    report = await run_checks(make_input({"finding_resolved": "true"}), tmp_path, timeout=30)
    assert report.facts == {"finding_resolved": True}
    assert report.evidence().facts == {"finding_resolved": True}


async def test_a_command_that_fails_yields_false_and_not_unknown(make_input, tmp_path: pathlib.Path) -> None:
    """No "we do not know": it is the same choice as a guarantee without substance, which refuses."""
    report = await run_checks(make_input({"finding_resolved": "false"}), tmp_path, timeout=30)
    assert report.facts == {"finding_resolved": False}
    assert report.evidence().facts == {"finding_resolved": False}


async def test_the_command_is_really_executed(make_input, tmp_path: pathlib.Path) -> None:
    """Otherwise the test would only prove the plumbing, not the measurement."""
    marker = tmp_path / "marker.txt"
    report = await run_checks(make_input({"file_written": f"touch {marker.name}"}), tmp_path, timeout=30)
    assert marker.exists(), "the command did not run in the workspace"
    assert report.facts == {"file_written": True}


async def test_without_a_command_no_fact_is_measured(make_input, tmp_path: pathlib.Path) -> None:
    """And above all: `facts` must stay `None`, otherwise the merge erases the agent's."""
    report = await run_checks(make_input({}), tmp_path, timeout=30)
    assert report.facts == {}
    assert report.evidence().facts is None


async def test_an_empty_command_is_ignored(make_input, tmp_path: pathlib.Path) -> None:
    report = await run_checks(make_input({"never": "   "}), tmp_path, timeout=30)
    assert report.facts == {}


def test_the_measured_fact_overwrites_the_agents() -> None:
    declared = Evidence(facts={"finding_resolved": True, "report_attached": True})
    measured = Evidence(facts={"finding_resolved": False})
    merged = merge_evidence(declared, measured)
    assert merged.facts == {"finding_resolved": False, "report_attached": True}, (
        "the measurement must win over the declaration, AND the unmeasured facts must survive"
    )


def test_without_a_measured_fact_the_agents_survive() -> None:
    declared = Evidence(facts={"profiles_kept": 3})
    merged = merge_evidence(declared, Evidence(tests_passed=True))
    assert merged.facts == {"profiles_kept": 3}
    assert merged.tests_passed is True


@pytest.mark.parametrize("value", (True, False))
async def test_the_evidence_facts_guarantee_judges_the_measurement(
    value: bool, make_input, tmp_path: pathlib.Path
) -> None:
    """The end of the chain: the guarantee rules on the MEASURED fact, not on the declared one."""
    from choregos_contracts import StageResult
    from choregos_core.gates import GateContext, evaluate

    command = "true" if value else "false"
    report = await run_checks(make_input({"finding_resolved": command}), tmp_path, timeout=30)
    # The agent claims the opposite of the measurement: that is the whole point.
    declared = Evidence(facts={"finding_resolved": not value})
    result = StageResult(
        schema="choregos/StageResult/v1",
        status="done",
        summary="verification",
        evidence=merge_evidence(declared, report.evidence()),
    )
    verdict = evaluate(
        "evidence_facts",
        GateContext(result=result),
        {"keys": ["finding_resolved"], "must_be_true": ["finding_resolved"]},
    )
    assert verdict.passed is value, "the guarantee followed the declaration, not the measurement"
    if not value:
        assert verdict.blocking, "a measured false fact must BLOCK, not merely fail to pass"
