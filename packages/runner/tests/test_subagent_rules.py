"""S67-12: every backend the runner ships has a sub-agent rule (or an explicit `None`), kept in core where
the API derives the nodes (the API image does not import the runner)."""

from __future__ import annotations

from choregos_core.agent_tree import RULES
from choregos_runner.backends import BACKENDS, backend_version


def test_every_backend_has_an_entry_in_the_rules() -> None:
    assert set(BACKENDS) <= set(RULES), set(BACKENDS) - set(RULES)


def test_a_rule_names_the_adapter_version_the_image_pins() -> None:
    """A bump of the adapter must re-read its source: the rule's `source` carries the version."""
    for name in ("claude-code", "opencode"):
        rule = RULES[name]
        assert rule is not None
        version = backend_version(name)
        assert version is not None and version in rule.source, (name, version, rule.source)
