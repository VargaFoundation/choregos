"""A stage result's software evidence as named measured facts, beside the fields, for one window
(ADR 0181 C2, ADR 0056): `Evidence.value` reads the fact first, `with_facts` writes both forms."""

from __future__ import annotations

from tranibor_contracts import Evidence
from tranibor_contracts.stage import WorkItemLinks
from tranibor_contracts.window import C2_DELIVERY, DELIVERY_EVIDENCE, WINDOW


def test_with_facts_writes_each_field_as_its_fact_and_says_which_were_measured() -> None:
    evidence = Evidence(
        tests_run=12, tests_passed=True, lint="ok", diff_files=3, measured=["tests_run", "diff_files"]
    ).with_facts()
    assert evidence.facts == {"tests_run": 12, "tests_passed": True, "lint": "ok", "diff_files": 3}
    assert {"facts.tests_run", "facts.diff_files"} <= set(evidence.measured or [])
    assert "facts.lint" not in (evidence.measured or []), "declared by the agent, not measured"
    assert evidence.tests_run == 12, "the field stays for the window"


def test_the_field_wins_over_an_agent_s_fact_of_the_same_name() -> None:
    evidence = Evidence(facts={"tests_run": 999, "profiles": 3}, tests_run=4, measured=["tests_run"])
    assert evidence.with_facts().facts == {"tests_run": 4, "profiles": 3}


def test_value_reads_the_fact_first_then_the_field() -> None:
    assert Evidence(facts={"tests_run": 7}).value("tests_run") == 7
    assert Evidence(tests_run=5).value("tests_run") == 5
    assert Evidence().value("coverage_delta") is None
    assert Evidence(facts={"tests_run": 7}, measured=["facts.tests_run"]).is_measured("tests_run")
    assert Evidence(tests_run=5, measured=["tests_run"]).is_measured("tests_run")
    assert Evidence(facts={"tests_passed": True, "tests_run": 2}).is_complete()
    assert Evidence(facts={"profiles": 1}).value("profiles") == 1
    assert Evidence(lint="ok").value("summary") is None, "only the evidence's names fall back"


def test_a_stage_input_names_the_change_request_under_either_name() -> None:
    assert WorkItemLinks(change_request="https://forge/pr/1").change_request_url() == "https://forge/pr/1"
    assert WorkItemLinks(pr="https://forge/pr/2").change_request_url() == "https://forge/pr/2"


def test_the_former_fields_are_on_the_window_list() -> None:
    names = {entry.name for entry in WINDOW if entry.window is C2_DELIVERY}
    assert {f"evidence.{n}" for n in DELIVERY_EVIDENCE} | {
        f"Evidence.{n}" for n in DELIVERY_EVIDENCE
    } <= names
    assert {"work_item.links.pr", "WorkItemLinks.pr"} <= names
    assert C2_DELIVERY.removed_on.isoformat() == "2027-05-07"
