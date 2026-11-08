"""What a project's workflows require of its connectors (ADR 0034, S19-01).

The product review: the settings of an HR project showed `scm`, `ci`, `cd`. A project only has to
connect what its workflows read; each requirement says why.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from choregos_core import parse_workflow
from choregos_core.dsl import NOT_DEPLOYED, TEMPLATE_NAMES, template_yaml
from choregos_core.dsl.requirements import requirements
from choregos_core.gates import gate_needs, known_gates

STAFFING = Path(__file__).resolve().parents[3] / "demo/workflows/staffing.yaml"

ARRIVAL = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: arrival, version: 1 }
actors:
  coordinator: { type: agent, role: plan, model: "profile:standard" }
  hr: { type: human, group: hr, sla_hours: 24 }
states:
  request: { display: Request, kind: wait }
  plan: { display: Access plan }
  validated: { display: Validated, terminal: true }
transitions:
  - { id: t-plan, from: request, to: plan, by: coordinator, outputs: [plan], gates: [outputs_present] }
  - { id: t-validate, from: plan, to: validated, by: hr }
"""


def _capabilities(*texts: str) -> list[str]:
    return [e.capability for e in requirements(parse_workflow(t)[0] for t in texts)]


#: The core workflows that deliver code to production; `default-request` (ADR 0110), the `blank`
#: skeleton (ADR 0112) and the catalogue's audits, studies and documentation (S59-02) do not.
SOFTWARE_TEMPLATES = [name for name in TEMPLATE_NAMES if name not in NOT_DEPLOYED]


@pytest.mark.parametrize("name", SOFTWARE_TEMPLATES)
def test_a_software_template_requires_a_repository_and_a_train_not_a_ci(name: str) -> None:
    """The PR's checks are read through the repository: GitHub Actions is enough, no `ci` connector."""
    found = {e.capability: e for e in requirements([parse_workflow(template_yaml(name))[0]])}
    assert {"scm", "cd"} <= set(found)
    reasons = found["ci"].reasons if "ci" in found else ()
    assert all("provenance_signed" in reason for reason in reasons), reasons


def test_the_default_request_workflow_needs_no_repository_ci_or_cd() -> None:
    """ADR 0110 §2: a step that works in a repository, a production state or a pull request turns it red."""
    assert _capabilities(template_yaml("default-request")) == ["tracker", "runtime", "gateway"]


def test_an_hr_process_requires_neither_a_repository_nor_a_ci_nor_a_train() -> None:
    assert _capabilities(ARRIVAL) == ["tracker", "runtime", "gateway"]
    assert _capabilities(STAFFING.read_text(encoding="utf-8")) == ["tracker", "runtime", "gateway"]


def test_each_requirement_says_why_and_where_from() -> None:
    (scm,) = [
        e for e in requirements([parse_workflow(template_yaml("default-simple"))[0]]) if e.capability == "scm"
    ]
    assert any("default-simple: the guarantee scope_respected" in reason for reason in scm.reasons)
    assert len(set(scm.reasons)) == len(scm.reasons), "without duplicates"


def test_a_guarantee_that_reads_a_prs_checks_requires_a_repository_even_in_an_hr_process() -> None:
    with_ci = ARRIVAL.replace("gates: [outputs_present]", "gates: [outputs_present, ci_green]")
    assert "scm" in _capabilities(with_ci) and "ci" not in _capabilities(with_ci)


def test_each_core_guarantee_knows_what_it_reads() -> None:
    """A guarantee that reads a diff or a CI without declaring it would leave a project without the
    connector it needs, and the guarantee would fail on the first run."""
    reads = {g: gate_needs(g) for g in known_gates()}
    assert reads["scope_respected"] == {"scm"}
    assert reads["ci_green"] == reads["scans_ok"] == {"scm"}, "the PR's checks, read through the repository"
    assert reads["outputs_present"] == frozenset()


def test_what_the_platform_does_is_required_by_the_effect_not_by_the_name() -> None:
    """#175: a renamed state keeps what it requires, because the effect is written (`does`, `production`)."""
    from choregos_core import parse_workflow
    from choregos_core.dsl import template_yaml
    from choregos_core.dsl.edition import edit
    from choregos_core.dsl.requirements import requirements

    text = edit(
        template_yaml("default-simple"),
        [
            {"op": "rename_state", "from": "pr_open", "to": "review"},
            {"op": "rename_state", "from": "deployed_prod", "to": "in_service"},
        ],
    ).yaml
    workflow, _ = parse_workflow(text, strict=False)
    reasons = {e.capability: " ".join(e.reasons) for e in requirements([workflow])}
    assert "opens a pull request" in reasons["scm"]
    assert "in_service is production" in reasons["cd"]


def test_the_architect_of_a_study_writes_in_the_repository() -> None:
    """It writes `docs/adr/NNNN-*.md` on a branch (S21-22): its project connects a repository."""
    study = ARRIVAL.replace("role: plan", "role: architect")
    (scm,) = [e for e in requirements([parse_workflow(study)[0]]) if e.capability == "scm"]
    assert any("coordinator (architect) works in a repository" in r for r in scm.reasons)


def test_a_documentation_step_works_in_the_repository() -> None:
    """The `document` role reads a diff and pushes a branch: it requires `scm` (S39-15)."""
    text = ARRIVAL.replace("role: plan", "role: document")
    assert "scm" in _capabilities(text)


def test_a_decision_step_needs_the_gateway_and_no_runtime_of_its_own() -> None:
    """ADR 0121: a decision runs in the orchestrator and asks its model through the gateway."""
    needs = _capabilities(template_yaml("decision-triage"))
    assert needs == ["tracker", "runtime", "gateway"], "the `deliver` agent still runs somewhere"
    reasons = {
        e.capability: e.reasons for e in requirements([parse_workflow(template_yaml("decision-triage"))[0]])
    }
    assert any("decision classifier" in r for r in reasons["gateway"])
    assert not any("classifier" in r for r in reasons["runtime"])
