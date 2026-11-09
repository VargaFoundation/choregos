# SPDX-License-Identifier: Apache-2.0
"""A fake step answers a typed output with a value its type accepts (ADR 0129)."""

from __future__ import annotations

from typing import Any

from choregos_adapters.fakes.executor import _typed_placeholder
from choregos_contracts import ArtifactDisplay, ArtifactLevel, TransitionRef


def _transition(**displays: ArtifactDisplay) -> TransitionRef:
    payload: dict[str, Any] = {"id": "t", "role": "triage", "from": "a", "to": "b"}
    return TransitionRef(**payload, artifact_displays=displays)


def test_each_type_gets_a_value_it_accepts() -> None:
    transition = _transition(
        level=ArtifactDisplay(type="level", levels=[ArtifactLevel(value="a"), ArtifactLevel(value="b")]),
        bounded=ArtifactDisplay(type="metric", min=5),
        free=ArtifactDisplay(type="metric"),
        pairs=ArtifactDisplay(type="keyvalue"),
    )
    assert _typed_placeholder(transition, "level", "x") == "a"
    assert _typed_placeholder(transition, "bounded", "x") == 5
    assert _typed_placeholder(transition, "free", "x") == 1
    assert _typed_placeholder(transition, "pairs", "x") == {"placeholder": "pairs"}
    assert _typed_placeholder(transition, "plain", "x") == "x"
