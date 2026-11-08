"""The core's workflow catalogue (S59-02, ADR 0111): each catalogue workflow is presented (title, summary,
tags), and every agent it names is an agent of the built-in catalogue the core ships (its offline
plugins, ADR 0093), so adopting it never asks for an agent the deployment cannot install."""

from __future__ import annotations

import pytest
from choregos_contracts import AgentActor
from choregos_core import parse_workflow
from choregos_core.agent_catalogue import entries
from choregos_core.dsl import NOT_DEPLOYED, TEMPLATE_NAMES, template_yaml

#: The catalogue of S59-02; the six earlier workflows are presented by their description only.
CATALOGUE = TEMPLATE_NAMES[TEMPLATE_NAMES.index("dev-feature") :]


def test_the_catalogue_covers_development_audit_and_more() -> None:
    assert len(CATALOGUE) == 13
    tags = {
        tag for name in CATALOGUE for tag in parse_workflow(template_yaml(name))[0].metadata.catalogue.tags
    }
    assert {"development", "audit", "security", "documentation", "incident", "architecture"} <= tags


@pytest.mark.parametrize("name", CATALOGUE)
def test_a_catalogue_workflow_is_presented(name: str) -> None:
    workflow = parse_workflow(template_yaml(name))[0]
    presented = workflow.metadata.catalogue
    assert presented is not None and presented.title and presented.summary and presented.tags
    assert ("production" in presented.tags) == (name not in NOT_DEPLOYED), "the tag says whether it deploys"


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_every_agent_a_core_workflow_names_is_in_the_built_in_catalogue(name: str) -> None:
    shipped = {entry.slug for entry in entries()}
    workflow = parse_workflow(template_yaml(name))[0]
    named = {a.agent.split("@")[0] for a in workflow.actors.values() if isinstance(a, AgentActor) and a.agent}
    assert named <= shipped, f"{name} names agents the core does not ship: {sorted(named - shipped)}"
