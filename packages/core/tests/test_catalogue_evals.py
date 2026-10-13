"""The catalogue's agents and their evals (S33-06, ADR 0020).

"Each agent has an eval that turns red when its instructions are emptied": proved here for every case,
by running it against a copy of the agent whose instructions are reduced to the ticket's context.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from choregos_core.catalogue_d_agents import DOSSIER, entree, entrees
from choregos_core.catalogue_d_agents.evals import case_paths, evaluate, load_case

INTERNAL = [e for e in entrees() if e.genre == "internal"]
CASES = {path.stem: load_case(path) for path in case_paths()}
#: The specialists of S33-06: each states its output contract and the tools it needs.
SPECIALISTS = ("secops", "compliance", "devops", "docs-writer", "ux-designer", "qa", "architect")


def test_every_internal_agent_has_an_eval() -> None:
    covered = {str(case["agent"]) for case in CASES.values()}
    assert {e.slug for e in INTERNAL} <= covered, {e.slug for e in INTERNAL} - covered


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_eval_is_green_on_the_shipped_instructions(name: str) -> None:
    result = evaluate(CASES[name])
    assert result.passed, (result.missing, result.forbidden)


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_eval_turns_red_when_the_instructions_are_emptied(name: str) -> None:
    case = CASES[name]
    agent = entree(str(case["agent"]))
    assert agent is not None
    context_only = (DOSSIER / "_contexte.md").read_text(encoding="utf-8")
    emptied = replace(
        agent, document={**agent.document, "spec": {**agent.document["spec"], "instructions": context_only}}
    )
    assert not evaluate(case, emptied).passed


@pytest.mark.parametrize("slug", SPECIALISTS)
def test_a_specialist_states_its_output_contract_and_its_tools(slug: str) -> None:
    agent = entree(slug)
    assert agent is not None and agent.outputs and agent.tools, slug
    instructions = agent.document["spec"]["instructions"]
    written = set(re.findall(r'"([a-z_]+)":', instructions))
    assert set(agent.outputs) <= written, f"{slug} declares outputs its instructions never ask for"


def test_compliance_is_followed_by_people_and_is_never_legal_advice() -> None:
    agent = entree("compliance")
    assert agent is not None and agent.human_gate_after
    assert "not legal advice" in agent.document["spec"]["instructions"]


def test_netops_is_not_duplicated_here() -> None:
    """S33-08 ships `netops`; this catalogue does not grow a second one."""
    assert [e.slug for e in entrees()].count("netops") <= 1
