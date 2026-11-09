"""`missing_components`: what an agent version leaves to its project (ADR 0127 §7, S60-04)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core.agent_completeness import COMPONENTS, is_complete, missing_components

COMPLETE: dict[str, Any] = {
    "instructions": "x",
    "model": "profile:by_size",
    "tools": {"catalogue": [], "extensions": [], "web": []},
    "memories": [],
}


def test_a_version_naming_everything_is_complete_even_with_empty_lists() -> None:
    assert missing_components(COMPLETE) == []
    assert is_complete(COMPLETE)


def test_a_bare_spec_misses_all_five_in_a_fixed_order() -> None:
    assert missing_components({"instructions": "x"}) == list(COMPONENTS)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda s: s.pop("model"), ["model"]),
        (lambda s: s.update(model="  "), ["model"]),
        (lambda s: s.update(model=None), ["model"]),
        (lambda s: s["tools"].pop("catalogue"), ["tools.catalogue"]),
        (lambda s: s["tools"].pop("extensions"), ["tools.extensions"]),
        (lambda s: s["tools"].pop("web"), ["tools.web"]),
        (lambda s: s["tools"].update(web=None), ["tools.web"]),
        (lambda s: s.pop("tools"), ["tools.catalogue", "tools.extensions", "tools.web"]),
        (
            lambda s: s.update(tools={"scripts": "offer"}),
            ["tools.catalogue", "tools.extensions", "tools.web"],
        ),
        (lambda s: s.pop("memories"), ["memories"]),
        (lambda s: s.update(memories=None), ["memories"]),
    ],
)
def test_each_component_is_required_on_its_own(mutate: Any, expected: list[str]) -> None:
    spec = {**COMPLETE, "tools": dict(COMPLETE["tools"])}
    mutate(spec)
    assert missing_components(spec) == expected


def test_the_legacy_memory_field_is_not_a_declaration() -> None:
    assert missing_components({**COMPLETE, "memories": None, "memory": "project"}) == ["memories"]


def test_a_remote_agent_is_complete_by_construction() -> None:
    assert missing_components({"runtime": {"kind": "a2a", "url": "https://x"}}) == []


def test_a_voice_agent_needs_only_its_model() -> None:
    assert missing_components({"runtime": {"kind": "voice"}}) == ["model"]
    assert missing_components({"runtime": {"kind": "voice"}, "model": "gpt-realtime"}) == []


def test_a_hosted_agent_declares_as_any_other() -> None:
    assert missing_components({"runtime": {"kind": "hosted"}, "model": "profile:strong"}) == [
        "tools.catalogue",
        "tools.extensions",
        "tools.web",
        "memories",
    ]
