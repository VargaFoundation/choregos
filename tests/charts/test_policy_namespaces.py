"""A Choregos policy that judges `choregos-*` namespaces also judges `choregos` itself (#911).

The glob `choregos-*` does not match a namespace named exactly `choregos`, which is where a
release is commonly installed (the dev cluster's is). Every rule of `infra/policies` scoped to
the platform's namespaces left the platform's own pods out. The Kyverno CLI cannot catch it:
1.19.1 reports a pod no rule matched as a pass ("Excluded"), so `make policies-test` refuses
that verdict, and this test reads every rule of every policy file.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
import yaml

POLICIES = pathlib.Path(__file__).resolve().parents[2] / "infra" / "policies"


def _namespace_lists(node: Any) -> Iterator[list[str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "namespaces" and isinstance(value, list):
                yield value
            else:
                yield from _namespace_lists(value)
    elif isinstance(node, list):
        for item in node:
            yield from _namespace_lists(item)


def _rules() -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(POLICIES.rglob("*.yaml")):
        if "tests" in path.relative_to(POLICIES).parts:
            continue
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if doc and doc.get("kind") == "ClusterPolicy":
                for rule in doc["spec"]["rules"]:
                    found.append((f"{path.name}:{rule['name']}", rule))
    return found


def test_the_policies_are_read() -> None:
    names = {name for name, _ in _rules()}
    assert "pod-security.yaml:non-root" in names, "the policies moved or changed shape"


@pytest.mark.parametrize(("name", "rule"), _rules(), ids=[name for name, _ in _rules()])
def test_a_rule_on_choregos_star_also_matches_choregos(name: str, rule: dict[str, Any]) -> None:
    for namespaces in _namespace_lists(rule.get("match", {})):
        if "choregos-*" in namespaces:
            assert "choregos" in namespaces, f"{name} judges `choregos-*` but not `choregos`"
