"""The "process" view: each transition said in plain words (ADR 0031)."""

from __future__ import annotations

import pathlib

from choregos_core import parse_workflow, to_process
from choregos_core.dsl import RESUMES
from choregos_core.gates import known_gates

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _staffing() -> list[dict[str, object]]:
    workflow, _ = parse_workflow((ROOT / "demo/workflows/staffing.yaml").read_text(encoding="utf-8"))
    return to_process(workflow)


def test_every_known_guarantee_has_a_summary() -> None:
    """A new guarantee without a summary would make the process view speak into the void."""
    assert sorted(set(known_gates()) - set(RESUMES)) == []


def test_an_agent_its_outputs_its_guarantees_and_its_retries() -> None:
    sourcing = next(e for e in _staffing() if e["to"] == "sourcing")
    assert sourcing["actor_type"] == "agent"
    phrase = str(sourcing["sentence"])
    assert "the agent `sourceur`" in phrase and "it produced `profils`" in phrase
    assert "the declared outputs are present" in phrase
    assert "retries up to 2 time(s)" in phrase and "“Needs a human”" in phrase


def test_a_person_their_delay_and_the_rejection() -> None:
    validation = next(e for e in _staffing() if e["from"] == "a_valider")
    assert validation["actor_type"] == "human"
    phrase = str(validation["sentence"])
    assert "a person of group `staffing-managers`, within 48 h" in phrase
    assert "If rejected, back to “Sourcing”" in phrase and "times out after 72 h" in phrase


def test_what_the_platform_does_is_said_in_plain_words() -> None:
    """`does` (#175): the process view says the platform opens, then merges the PR."""
    from choregos_core.dsl import template_yaml

    workflow, _ = parse_workflow(template_yaml("default-simple"))
    steps = {e["to"]: str(e["sentence"]) for e in to_process(workflow)}
    assert "the platform, which opens the pull request" in steps["pr_open"]
    assert "the platform, which merges the pull request" in steps["merged"]


def test_a_guarantee_says_its_thresholds_and_how_long_it_waits() -> None:
    """The map listed `diff_size_max` and `ci_green` as bare names: what the limit is, and that the
    step waits for the CI, were not said anywhere."""
    from choregos_core.dsl.process import gate_detail

    assert (
        gate_detail("diff_size_max", {})
        == "at most 60 files and 100000 lines changed (the platform's defaults)"
    )
    assert (
        gate_detail("diff_size_max", {"files": 10, "lines": 400}) == "at most 10 files and 400 lines changed"
    )
    assert "with none declared, anything passes" in str(gate_detail("scope_respected", {}))
    assert gate_detail("ci_green", {}, 72) == "waits for it up to 72 h"
    assert gate_detail("ci_green", {}) == "waits for it up to 24 h"
    assert gate_detail("no_secrets", {}) is None


def test_each_step_s_guarantees_carry_their_detail() -> None:
    for step in _staffing():
        for guarantee in step["gates"]:  # type: ignore[attr-defined]
            assert set(guarantee) == {"name", "summary", "detail", "asynchronous"}
