# SPDX-License-Identifier: Apache-2.0
"""The namespace grammar of memories (S32-02, S40-11)."""

from __future__ import annotations

import pytest
from choregos_core.memory_scope import (
    global_store_namespace,
    memory_namespace,
    parse_namespace,
    project_store_namespace,
    split_namespace,
)


def test_the_legacy_forms_are_unchanged() -> None:
    assert memory_namespace("billing", "project", "x") == "billing"
    assert memory_namespace("billing", "agent", "x") == "billing#agent:x"
    assert memory_namespace("billing", "none", "x") is None
    assert split_namespace("billing") == ("billing", None)
    assert split_namespace("billing#agent:x") == ("billing", "x")


def test_round_trips() -> None:
    project = parse_namespace("billing")
    assert (project.kind, project.project, project.agent, project.store) == ("project", "billing", None, None)
    agent = parse_namespace("billing#agent:architect")
    assert (agent.kind, agent.project, agent.agent) == ("agent", "billing", "architect")
    named = parse_namespace(project_store_namespace("billing", "decisions"))
    assert (named.kind, named.project, named.store, named.org) == (
        "project_store",
        "billing",
        "decisions",
        None,
    )
    glob = parse_namespace(global_store_namespace("varga", "lessons"))
    assert (glob.kind, glob.org, glob.store, glob.project) == ("global_store", "varga", "lessons", None)


@pytest.mark.parametrize("namespace", ["billing#memory:decisions", "org:varga#memory:lessons"])
def test_split_namespace_refuses_the_new_forms(namespace: str) -> None:
    """Answering `(project, None)` would read a named memory as the project's shared one."""
    with pytest.raises(ValueError, match="parse_namespace"):
        split_namespace(namespace)


@pytest.mark.parametrize("namespace", ["", "org:varga", "org:#memory:x", "billing#memory:", "billing#agent:"])
def test_malformed_namespaces_raise(namespace: str) -> None:
    with pytest.raises(ValueError):
        parse_namespace(namespace)
