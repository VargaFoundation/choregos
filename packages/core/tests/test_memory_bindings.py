# SPDX-License-Identifier: Apache-2.0
"""The pure rules of an agent's memory bindings (S40-11, ADR 0075)."""

from __future__ import annotations

import pytest
from choregos_contracts import ContextPack, MemoryItem
from choregos_core.memory_bindings import (
    Binding,
    binding_problems,
    effective_bindings,
    legacy_scope,
    merge_context_packs,
    namespace_of_binding,
)


@pytest.mark.parametrize(
    ("memory", "expected"),
    [
        ("project", [Binding("project", "shared", "write")]),
        ("agent", [Binding("project", "own", "write")]),
        ("none", []),
    ],
)
def test_the_legacy_memory_field_derives_its_bindings(memory: str, expected: list[Binding]) -> None:
    assert effective_bindings(None, memory) == expected
    assert legacy_scope(expected) == memory


def test_explicit_bindings_win_over_the_legacy_field() -> None:
    explicit = [Binding("global", "lessons", "read")]
    assert effective_bindings(explicit, "agent") == explicit
    assert effective_bindings([], "project") == []
    assert legacy_scope(explicit) is None
    assert legacy_scope([Binding("project", "shared", "read")]) is None


def test_problems() -> None:
    nine = [Binding("global", f"mem-{i}") for i in range(9)]
    assert any("at most 8" in p for p in binding_problems(nine))
    assert binding_problems(nine[:8]) == []
    twice = [Binding("global", "a-b"), Binding("global", "a-b", "write")]
    assert any("bound twice" in p for p in binding_problems(twice))
    two_writes = [Binding("global", "a-b", "write"), Binding("project", "shared", "write")]
    assert any("at most one memory may be written" in p for p in binding_problems(two_writes))
    assert any("reserved" in p for p in binding_problems([Binding("global", "shared")]))
    assert binding_problems([Binding("project", "shared", "write"), Binding("global", "lessons")]) == []


def test_namespaces_of_bindings() -> None:
    def ns(binding: Binding, agent: str | None = "architect") -> str | None:
        return namespace_of_binding("varga", "billing", agent, binding)

    assert ns(Binding("project", "shared")) == "billing"
    assert ns(Binding("project", "own")) == "billing#agent:architect"
    assert ns(Binding("project", "own"), None) is None
    assert ns(Binding("project", "decisions")) == "billing#memory:decisions"
    assert ns(Binding("global", "lessons")) == "org:varga#memory:lessons"


def _item(subject: str, score: float, kind: str = "decision", content: str = "x" * 40) -> MemoryItem:
    return MemoryItem(kind=kind, subject=subject, content=content, score=score)


def _pack(*items: MemoryItem, truncated: bool = False) -> ContextPack:
    return ContextPack(
        query="q",
        memories=[i for i in items if i.kind != "incident"],
        incidents=[i for i in items if i.kind == "incident"],
        truncated=truncated,
    )


def test_precedence_wins_a_duplicated_subject() -> None:
    first = _pack(_item("same", 0.1, content="from the first"))
    second = _pack(_item("same", 0.9, content="from the second"), _item("other", 0.5))
    merged = merge_context_packs([("global:a", first), ("project:b", second)], 1000)
    by_subject = {i.subject: i for i in merged.memories}
    assert by_subject["same"].content == "from the first"
    assert by_subject["same"].memory == "global:a"
    assert by_subject["other"].memory == "project:b"


def test_round_robin_by_precedence_under_a_budget_of_three_items() -> None:
    m1 = _pack(_item("m1-1", 0.9), _item("m1-2", 0.8), _item("m1-3", 0.7))
    m2 = _pack(_item("m2-1", 0.2), _item("m2-2", 0.1))
    # 40 characters = 10 tokens each; 30 tokens fit three items.
    merged = merge_context_packs([("global:m1", m1), ("project:m2", m2)], 30)
    assert [i.subject for i in merged.memories] == ["m1-1", "m2-1", "m1-2"]
    assert merged.truncated is True
    assert merged.tokens_estimated == 30
    assert merged.budget_tokens == 30


def test_a_large_memory_does_not_crowd_the_others_out() -> None:
    big = _pack(*[_item(f"big-{i}", 1.0 - i / 100) for i in range(20)])
    small = _pack(_item("small-1", 0.01))
    merged = merge_context_packs([("global:big", big), ("project:small", small)], 50)
    assert "small-1" in {i.subject for i in merged.memories}


def test_an_empty_pack_is_ignored_and_labels_are_set() -> None:
    merged = merge_context_packs(
        [
            ("global:a", ContextPack.empty("q")),
            ("project:b", _pack(_item("only", 0.3))),
            ("project:c", _pack()),
        ],
        100,
    )
    assert [(i.subject, i.memory) for i in merged.memories] == [("only", "project:b")]
    assert merged.truncated is False
    assert merge_context_packs([], 100).is_empty()


def test_incidents_stay_incidents() -> None:
    merged = merge_context_packs([("global:a", _pack(_item("boom", 0.5, kind="incident")))], 100)
    assert [i.subject for i in merged.incidents] == ["boom"] and merged.memories == []
